#!/usr/bin/env python3
"""Keyboard teleop for the tentone Tello driver — with built-in keepalive.

Publishes geometry_msgs/Twist on /control at 10 Hz continuously (zeros when
idle), which doubles as the SDK keepalive: the Tello auto-lands after ~15 s
without RC commands, so just keeping this node running prevents that.

Run (driver must already be running):
    source ~/tello-drone/scripts/ros_env.sh
    python3 ~/tello-drone/scripts/tello_teleop.py

Keys (hold to move — motion stops ~0.4 s after release):
    t : takeoff                l : land
    w / s : forward / back     a / d : strafe left / right
    q / e : yaw left / right   r / f : up / down
    space : stop (hover)       + / - : speed up / down
    x : quit (land first! quitting stops the keepalive -> auto-land in ~15 s)

Driver mapping (tentone cb_control -> djitellopy send_rc_control):
    linear.x = left/right   linear.y = forward/back
    linear.z = up/down      angular.z = yaw
"""

import sys
import select
import termios
import time
import tty

import rclpy
from geometry_msgs.msg import Twist
from std_msgs.msg import Empty
from sensor_msgs.msg import BatteryState

PUBLISH_HZ = 10.0
HOLD_TIMEOUT = 0.4   # seconds after last keypress before motion zeroes
SPEED_DEFAULT = 30   # tello rc range is -100..100
SPEED_STEP = 10

# key -> (attr, axis, sign)
MOTION_KEYS = {
    'w': ('linear', 'y', +1),   # forward
    's': ('linear', 'y', -1),   # back
    'a': ('linear', 'x', -1),   # strafe left
    'd': ('linear', 'x', +1),   # strafe right
    'r': ('linear', 'z', +1),   # up
    'f': ('linear', 'z', -1),   # down
    'q': ('angular', 'z', -1),  # yaw left
    'e': ('angular', 'z', +1),  # yaw right
}


def main():
    if not sys.stdin.isatty():
        sys.exit('tello_teleop needs an interactive terminal (stdin is not a tty)')

    rclpy.init()
    node = rclpy.create_node('tello_teleop')
    pub_control = node.create_publisher(Twist, '/control', 10)
    pub_takeoff = node.create_publisher(Empty, '/takeoff', 10)
    pub_land = node.create_publisher(Empty, '/land', 10)

    battery = {'pct': None}

    def on_battery(msg):
        battery['pct'] = msg.percentage

    node.create_subscription(BatteryState, '/battery', on_battery, 10)

    speed = SPEED_DEFAULT
    active_key = None
    last_key_time = 0.0
    last_pub = 0.0
    last_status = ''

    print(__doc__)
    print('Publishing keepalive on /control at 10 Hz. Press t to takeoff.\n')

    old_settings = termios.tcgetattr(sys.stdin)
    try:
        tty.setcbreak(sys.stdin.fileno())
        period = 1.0 / PUBLISH_HZ
        while True:
            # Drain all pending keys (terminal autorepeat refreshes the hold)
            key = None
            while select.select([sys.stdin], [], [], 0)[0]:
                key = sys.stdin.read(1)
            if key is None:
                # small wait so the loop doesn't spin
                select.select([sys.stdin], [], [], period * 0.5)

            now = time.monotonic()

            if key == 'x':
                break
            elif key == 't':
                pub_takeoff.publish(Empty())
                print('\n>> TAKEOFF')
            elif key == 'l':
                pub_land.publish(Empty())
                active_key = None
                print('\n>> LAND')
            elif key == ' ':
                active_key = None
            elif key in ('+', '='):
                speed = min(100, speed + SPEED_STEP)
            elif key in ('-', '_'):
                speed = max(10, speed - SPEED_STEP)
            elif key in MOTION_KEYS:
                active_key = key
                last_key_time = now

            if active_key and (now - last_key_time) > HOLD_TIMEOUT:
                active_key = None

            if now - last_pub >= period:
                twist = Twist()
                if active_key:
                    attr, axis, sign = MOTION_KEYS[active_key]
                    setattr(getattr(twist, attr), axis, float(sign * speed))
                pub_control.publish(twist)
                last_pub = now

            rclpy.spin_once(node, timeout_sec=0)

            bat = f'{battery["pct"]:.0f}%' if battery['pct'] is not None else '--'
            status = (f'speed={speed:3d}  moving={active_key or "hover"}  '
                      f'battery={bat}   (t/l takeoff/land, x quit)')
            if status != last_status:
                print('\r' + status.ljust(78), end='', flush=True)
                last_status = status
    except KeyboardInterrupt:
        pass
    finally:
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
        # leave the drone hovering with a final zero, then warn
        pub_control.publish(Twist())
        print('\n\nteleop stopped — keepalive is OFF. If still flying, the '
              'Tello auto-lands in ~15 s (or restart teleop / press l first).')
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
