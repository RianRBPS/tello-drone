"""
mission_planner node
====================
Autonomous grid mission for the Tello drone.

State machine
-------------
IDLE → TAKEOFF → FLY → SETTLE → CAPTURE → NEXT → (FLY loop) → LAND → DONE
                                                              ↘ EMERGENCY (low battery)

Topics
------
  Sub  /rtabmap/odom          nav_msgs/Odometry      position feedback
  Sub  /flight_data           tello_msgs/FlightData  battery + barometer altitude
  Pub  /cmd_vel               geometry_msgs/Twist    velocity commands
  Pub  /mosaic_capture/trigger std_msgs/Empty        capture trigger
  Pub  /mission_status        std_msgs/String        human-readable status

Services
--------
  Client  /tello_action       tello_msgs/TelloAction  takeoff / land

Parameters (see config/mission_params.yaml)
-------------------------------------------
  grid_rows, grid_cols, grid_step_m
  altitude_m, max_speed_mps
  position_tol_m      reach threshold for a waypoint
  settle_time_s       seconds to hover after reaching waypoint before capture
  battery_land_pct    emergency land threshold
  kp_xy, kd_xy, max_xy_mps
  kp_z,  kd_z,  max_z_mps
"""

import math
import time
from enum import Enum, auto

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy

from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
from std_msgs.msg import Empty, String
from tello_msgs.srv import TelloAction
from tello_msgs.msg import FlightData

from .controller import PositionController
from .grid import generate_grid, describe_grid


# ── state machine ─────────────────────────────────────────────────────────────

class State(Enum):
    IDLE      = auto()
    TAKEOFF   = auto()
    FLY       = auto()
    SETTLE    = auto()
    CAPTURE   = auto()
    NEXT      = auto()
    LAND      = auto()
    EMERGENCY = auto()
    DONE      = auto()


# ── node ──────────────────────────────────────────────────────────────────────

class MissionPlanner(Node):

    def __init__(self):
        super().__init__('mission_planner')

        # ── parameters ────────────────────────────────────────────────────────
        self.declare_parameter('grid_rows',       3)
        self.declare_parameter('grid_cols',       3)
        self.declare_parameter('grid_step_m',     1.0)
        self.declare_parameter('altitude_m',      1.2)
        self.declare_parameter('max_speed_mps',   0.3)
        self.declare_parameter('position_tol_m',  0.25)
        self.declare_parameter('settle_time_s',   1.5)
        self.declare_parameter('battery_land_pct', 20)
        self.declare_parameter('kp_xy',  0.4)
        self.declare_parameter('kd_xy',  0.1)
        self.declare_parameter('max_xy_mps', 0.3)
        self.declare_parameter('kp_z',   0.5)
        self.declare_parameter('kd_z',   0.1)
        self.declare_parameter('max_z_mps',  0.3)

        p = self.get_parameters

        self._altitude    = p(['altitude_m'])[0].value
        self._pos_tol     = p(['position_tol_m'])[0].value
        self._settle_time = p(['settle_time_s'])[0].value
        self._bat_thresh  = p(['battery_land_pct'])[0].value
        max_spd           = p(['max_speed_mps'])[0].value

        rows  = p(['grid_rows'])[0].value
        cols  = p(['grid_cols'])[0].value
        step  = p(['grid_step_m'])[0].value

        self._waypoints: list[tuple[float, float]] = generate_grid(rows, cols, step)
        self._wp_idx = 0

        self._ctrl = PositionController(
            kp_xy=p(['kp_xy'])[0].value,  kd_xy=p(['kd_xy'])[0].value,  max_xy=min(max_spd, p(['max_xy_mps'])[0].value),
            kp_z=p(['kp_z'])[0].value,    kd_z=p(['kd_z'])[0].value,    max_z=p(['max_z_mps'])[0].value,
        )

        # ── state ─────────────────────────────────────────────────────────────
        self._state     = State.IDLE
        self._pos       = (0.0, 0.0, 0.0)   # x, y, z from odom
        self._baro_z    = 0.0               # altitude from barometer
        self._battery   = 100
        self._settle_ts: float | None = None
        self._start_pos: tuple[float, float] = (0.0, 0.0)

        # ── publishers ────────────────────────────────────────────────────────
        self._cmd_pub    = self.create_publisher(Twist,  '/cmd_vel', 10)
        self._trig_pub   = self.create_publisher(Empty,  '/mosaic_capture/trigger', 10)
        self._status_pub = self.create_publisher(String, '/mission_status', 10)

        # ── service client ────────────────────────────────────────────────────
        self._tello_cli = self.create_client(TelloAction, '/tello_action')

        # ── subscribers ───────────────────────────────────────────────────────
        best_effort = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=1,
        )
        self.create_subscription(Odometry,   '/rtabmap/odom', self._on_odom,   10)
        self.create_subscription(FlightData, '/flight_data',  self._on_fd,     best_effort)

        # ── control loop: 10 Hz ───────────────────────────────────────────────
        self.create_timer(0.1, self._loop)

        self.get_logger().info(
            f'MissionPlanner ready | {describe_grid(rows, cols, step)} | '
            f'alt={self._altitude} m | tol={self._pos_tol} m'
        )
        self._publish_status('IDLE — call /tello_action takeoff to begin')

    # ── callbacks ─────────────────────────────────────────────────────────────

    def _on_odom(self, msg: Odometry):
        p = msg.pose.pose.position
        self._pos = (p.x, p.y, p.z)

    def _on_fd(self, msg: FlightData):
        self._battery = msg.bat
        # barometer gives relative altitude in cm → convert to metres
        self._baro_z = msg.baro / 100.0

        if self._battery < self._bat_thresh and self._state not in (
            State.LAND, State.EMERGENCY, State.DONE, State.IDLE
        ):
            self.get_logger().warn(
                f'Battery {self._battery}% < {self._bat_thresh}% — emergency land!'
            )
            self._transition(State.EMERGENCY)

    # ── main control loop ─────────────────────────────────────────────────────

    def _loop(self):
        if self._state == State.IDLE:
            # Wait for external takeoff trigger via /tello_action
            # (user runs: ros2 service call /tello_action tello_msgs/TelloAction "{cmd: 'takeoff'}")
            # Once the drone is airborne, transition to FLY
            if self._baro_z > 0.3:
                self._start_pos = (self._pos[0], self._pos[1])
                # shift grid so first waypoint = current position
                ox, oy = self._start_pos
                self._waypoints = [(x + ox, y + oy) for x, y in self._waypoints]
                self.get_logger().info('Altitude detected — starting mission')
                self._transition(State.FLY)

        elif self._state == State.TAKEOFF:
            # Not used currently (user does manual takeoff)
            # Kept as a hook for fully-automated future use
            pass

        elif self._state == State.FLY:
            self._fly_toward_waypoint()

        elif self._state == State.SETTLE:
            self._hover_hold()
            if self._settle_ts and (time.monotonic() - self._settle_ts) >= self._settle_time:
                self._transition(State.CAPTURE)

        elif self._state == State.CAPTURE:
            self._trig_pub.publish(Empty())
            self.get_logger().info(
                f'Capture triggered at waypoint {self._wp_idx + 1}/{len(self._waypoints)}'
            )
            self._transition(State.NEXT)

        elif self._state == State.NEXT:
            self._wp_idx += 1
            if self._wp_idx >= len(self._waypoints):
                self.get_logger().info('All waypoints done — landing')
                self._transition(State.LAND)
            else:
                self._ctrl.reset()
                self._transition(State.FLY)

        elif self._state in (State.LAND, State.EMERGENCY):
            self._stop()
            self._send_tello_cmd('land')
            self._transition(State.DONE)

        elif self._state == State.DONE:
            pass   # sit quietly

    # ── motion helpers ────────────────────────────────────────────────────────

    def _fly_toward_waypoint(self):
        if self._wp_idx >= len(self._waypoints):
            return

        tx, ty = self._waypoints[self._wp_idx]
        cx, cy, _ = self._pos

        dist = math.hypot(tx - cx, ty - cy)
        self._publish_status(
            f'FLY wp {self._wp_idx + 1}/{len(self._waypoints)} '
            f'| dist={dist:.2f} m | bat={self._battery}%'
        )

        if dist <= self._pos_tol:
            self._stop()
            self._settle_ts = time.monotonic()
            self._transition(State.SETTLE)
            return

        vx, vy, vz = self._ctrl.compute(
            tx, ty, self._altitude,
            cx, cy, self._baro_z,
        )
        self._send_vel(vx, vy, vz)

    def _hover_hold(self):
        """Hold position while settling."""
        cx, cy, _ = self._pos
        tx, ty = self._waypoints[self._wp_idx]
        vx, vy, vz = self._ctrl.compute(
            tx, ty, self._altitude,
            cx, cy, self._baro_z,
        )
        self._send_vel(vx, vy, vz)

    # ── low-level send helpers ─────────────────────────────────────────────────

    def _send_vel(self, vx: float, vy: float, vz: float):
        msg = Twist()
        msg.linear.x = vx
        msg.linear.y = vy
        msg.linear.z = vz
        self._cmd_pub.publish(msg)

    def _stop(self):
        self._send_vel(0.0, 0.0, 0.0)

    def _send_tello_cmd(self, cmd: str):
        if not self._tello_cli.wait_for_service(timeout_sec=2.0):
            self.get_logger().error('/tello_action service not available')
            return
        req = TelloAction.Request()
        req.cmd = cmd
        future = self._tello_cli.call_async(req)
        future.add_done_callback(
            lambda f: self.get_logger().info(f'tello_action {cmd} rc={f.result().rc}')
        )

    # ── state + status ────────────────────────────────────────────────────────

    def _transition(self, new_state: State):
        self.get_logger().info(f'{self._state.name} → {new_state.name}')
        self._state = new_state
        self._publish_status(new_state.name)

    def _publish_status(self, text: str):
        msg = String()
        msg.data = text
        self._status_pub.publish(msg)


# ── entry point ───────────────────────────────────────────────────────────────

def main():
    rclpy.init()
    node = MissionPlanner()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node._stop()
        node.destroy_node()
        rclpy.shutdown()
