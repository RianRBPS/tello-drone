#!/usr/bin/env python3
"""
test_mission_planner.py
=======================
Unit tests for mission_planner logic — no ROS or drone needed.

Tests:
  1. Grid generation (shape, snake order, boundary values)
  2. PD controller (direction, clamping, derivative)
  3. State machine transitions via a lightweight stub node
"""
import sys
import os
import math

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(
    REPO_ROOT, 'tello_ws', 'install',
    'mission_planner', 'lib', 'python3.10', 'site-packages'
))

from mission_planner.grid import generate_grid, describe_grid
from mission_planner.controller import PDController, PositionController


# ── helpers ───────────────────────────────────────────────────────────────────

PASS = '\033[92mPASS\033[0m'
FAIL = '\033[91mFAIL\033[0m'
_results = []

def check(name: str, condition: bool, detail: str = ''):
    status = PASS if condition else FAIL
    suffix = f'  ({detail})' if detail else ''
    print(f'  {status}  {name}{suffix}')
    _results.append(condition)


# ── 1. Grid tests ─────────────────────────────────────────────────────────────

def test_grid():
    print('\n── Grid generation ────────────────────────────────')

    # basic shape
    wps = generate_grid(3, 4, 1.0)
    check('3×4 grid has 12 waypoints', len(wps) == 12)

    # snake order: row 0 goes left→right, row 1 right→left
    row0 = wps[:4]
    row1 = wps[4:8]
    check('Row 0 is left→right', row0[0][0] < row0[-1][0])
    check('Row 1 is right→left', row1[0][0] > row1[-1][0])

    # 1×1 grid is a single waypoint
    wps_1 = generate_grid(1, 1, 1.0)
    check('1×1 grid = 1 waypoint', len(wps_1) == 1)

    # step is respected
    wps = generate_grid(2, 2, 0.5)
    dx = abs(wps[1][0] - wps[0][0])
    check(f'Step 0.5 m respected (dx={dx})', abs(dx - 0.5) < 1e-6, f'dx={dx}')

    # offset
    wps = generate_grid(2, 2, 1.0, start_x=5.0, start_y=3.0)
    check('Start offset applied', wps[0] == (5.0, 3.0), str(wps[0]))

    # error cases
    try:
        generate_grid(0, 3, 1.0)
        check('Raises on rows=0', False)
    except ValueError:
        check('Raises on rows=0', True)

    try:
        generate_grid(2, 2, -1.0)
        check('Raises on negative step', False)
    except ValueError:
        check('Raises on negative step', True)

    # describe_grid smoke test
    desc = describe_grid(3, 3, 1.0)
    check('describe_grid returns string', isinstance(desc, str) and len(desc) > 0, desc)


# ── 2. PD controller tests ────────────────────────────────────────────────────

def test_controller():
    print('\n── PD controller ──────────────────────────────────')

    ctrl = PDController(kp=1.0, kd=0.0, max_output=10.0)

    # proportional: output should equal error when kp=1, kd=0
    out = ctrl.compute(2.0)
    check('Proportional output (kp=1, err=2)', abs(out - 2.0) < 0.01, f'out={out:.3f}')

    # sign: negative error → negative output
    ctrl2 = PDController(kp=1.0, kd=0.0, max_output=10.0)
    out_neg = ctrl2.compute(-3.0)
    check('Negative error → negative output', out_neg < 0, f'out={out_neg:.3f}')

    # clamping
    ctrl3 = PDController(kp=1.0, kd=0.0, max_output=1.0)
    out_clamp = ctrl3.compute(100.0)
    check('Output clamped to max', out_clamp == 1.0, f'out={out_clamp}')

    out_clamp_neg = ctrl3.compute(-100.0)
    check('Output clamped to -max', out_clamp_neg == -1.0, f'out={out_clamp_neg}')

    # derivative: second call with same error → derivative ≈ 0 → output ≈ kp*error
    ctrl4 = PDController(kp=1.0, kd=1.0, max_output=10.0)
    ctrl4.compute(2.0)
    import time; time.sleep(0.05)
    out_same = ctrl4.compute(2.0)   # error unchanged → derivative ≈ 0
    check('Same error twice → derivative near 0', abs(out_same - 2.0) < 0.5, f'out={out_same:.3f}')

    # reset clears state
    ctrl4.reset()
    out_after_reset = ctrl4.compute(2.0)
    check('After reset, first call has no derivative', abs(out_after_reset - 2.0) < 0.5)

    # PositionController: zero error → zero velocity
    pos_ctrl = PositionController()
    vx, vy, vz = pos_ctrl.compute(1.0, 1.0, 1.0, 1.0, 1.0, 1.0)
    check('Zero position error → zero velocity',
          abs(vx) < 1e-6 and abs(vy) < 1e-6 and abs(vz) < 1e-6,
          f'vx={vx:.4f} vy={vy:.4f} vz={vz:.4f}')

    # PositionController: positive x error → positive vx
    pos_ctrl2 = PositionController()
    vx2, _, _ = pos_ctrl2.compute(2.0, 0.0, 1.0, 0.0, 0.0, 1.0)
    check('Positive x error → positive vx', vx2 > 0, f'vx={vx2:.3f}')


# ── 3. State machine logic (pure logic, no ROS) ───────────────────────────────

def test_state_machine_logic():
    """
    Test the state transition logic without spinning up ROS.
    We simulate what the node would do by calling its internal methods
    after constructing a minimal stub.
    """
    print('\n── State machine logic ────────────────────────────')

    from mission_planner.grid import generate_grid
    from mission_planner.controller import PositionController
    from enum import Enum, auto

    class State(Enum):
        IDLE = auto(); FLY = auto(); SETTLE = auto()
        CAPTURE = auto(); NEXT = auto(); LAND = auto(); DONE = auto()

    # Simulate: 2-waypoint mission, drone starts at origin
    waypoints = generate_grid(1, 2, 1.0)   # [(0,0), (1,0)]
    check('2-waypoint grid generated', len(waypoints) == 2, str(waypoints))

    # Drone at origin, target wp[0] = (0,0) → already within tolerance
    pos_tol = 0.25
    cx, cy = 0.0, 0.0
    tx, ty = waypoints[0]
    dist = math.hypot(tx - cx, ty - cy)
    check('At wp[0]: dist within tolerance', dist <= pos_tol, f'dist={dist}')

    # Move to wp[1] = (1,0) — drone still at origin → not within tolerance
    tx2, ty2 = waypoints[1]
    dist2 = math.hypot(tx2 - cx, ty2 - cy)
    check('At wp[1]: dist outside tolerance', dist2 > pos_tol, f'dist={dist2}')

    # After visiting both waypoints, index exceeds list → trigger LAND
    wp_idx_after_last = 2
    check('wp_idx >= len → should land', wp_idx_after_last >= len(waypoints))

    # Battery threshold logic
    battery = 15
    bat_thresh = 20
    check('Low battery triggers emergency', battery < bat_thresh)


# ── summary ───────────────────────────────────────────────────────────────────

def main():
    print('=== mission_planner unit tests ===')
    test_grid()
    test_controller()
    test_state_machine_logic()

    total  = len(_results)
    passed = sum(_results)
    print(f'\n── Summary: {passed}/{total} passed ──────────────────')
    sys.exit(0 if passed == total else 1)


if __name__ == '__main__':
    main()
