"""
controller.py
=============
PD controllers for XY position and Z altitude hold.
Kept separate so gains can be tuned independently.
"""
import time


class PDController:
    """Single-axis PD controller with output clamping."""

    def __init__(self, kp: float, kd: float, max_output: float):
        self.kp = kp
        self.kd = kd
        self.max_output = max_output
        self._prev_error: float | None = None   # None = no previous sample
        self._prev_time:  float | None = None

    def reset(self):
        """Clear state — next compute() will have no derivative term."""
        self._prev_error = None
        self._prev_time  = None

    def compute(self, error: float) -> float:
        now = time.monotonic()
        if self._prev_time is None or self._prev_error is None:
            # First call (or after reset): no derivative, avoids control spike
            derivative = 0.0
        else:
            dt = max(now - self._prev_time, 1e-4)
            derivative = (error - self._prev_error) / dt

        output = self.kp * error + self.kd * derivative
        output = max(-self.max_output, min(self.max_output, output))

        self._prev_error = error
        self._prev_time = now
        return output


class PositionController:
    """
    3-axis position controller: XY from odometry, Z from barometer.
    Returns (vx, vy, vz) in m/s in the odom frame.
    """

    def __init__(self,
                 kp_xy: float = 0.4, kd_xy: float = 0.1, max_xy: float = 0.4,
                 kp_z:  float = 0.5, kd_z:  float = 0.1, max_z:  float = 0.3):
        self._cx = PDController(kp_xy, kd_xy, max_xy)
        self._cy = PDController(kp_xy, kd_xy, max_xy)
        self._cz = PDController(kp_z,  kd_z,  max_z)

    def reset(self):
        self._cx.reset()
        self._cy.reset()
        self._cz.reset()

    def compute(self,
                target_x: float, target_y: float, target_z: float,
                current_x: float, current_y: float, current_z: float,
                ) -> tuple[float, float, float]:
        vx = self._cx.compute(target_x - current_x)
        vy = self._cy.compute(target_y - current_y)
        vz = self._cz.compute(target_z - current_z)
        return vx, vy, vz

    def update_gains(self,
                     kp_xy=None, kd_xy=None, max_xy=None,
                     kp_z=None,  kd_z=None,  max_z=None):
        if kp_xy  is not None: self._cx.kp = self._cy.kp = kp_xy
        if kd_xy  is not None: self._cx.kd = self._cy.kd = kd_xy
        if max_xy is not None: self._cx.max_output = self._cy.max_output = max_xy
        if kp_z   is not None: self._cz.kp = kp_z
        if kd_z   is not None: self._cz.kd = kd_z
        if max_z  is not None: self._cz.max_output = max_z
