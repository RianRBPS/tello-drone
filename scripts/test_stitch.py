#!/usr/bin/env python3
"""
test_stitch.py
==============
Smoke-test for stitch_mosaic.py — no drone or ROS needed.

Creates 5 synthetic overlapping frames (a large checkerboard panned
left-to-right) and runs both stitching methods, reporting pass/fail.
"""
import os
import sys
import csv
import shutil
import subprocess
import numpy as np
import cv2

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEST_DIR  = os.path.join(REPO_ROOT, 'data', 'test_images')
SCRIPTS   = os.path.join(REPO_ROOT, 'scripts')


# ── synthetic image generation ───────────────────────────────────────────────

def make_checkerboard_scene(width=1920, height=720, square=60):
    """Large checkerboard scene we can 'fly' a crop window over."""
    scene = np.zeros((height, width, 3), dtype=np.uint8)
    for r in range(0, height, square):
        for c in range(0, width, square):
            if (r // square + c // square) % 2 == 0:
                scene[r:r+square, c:c+square] = (200, 200, 200)
    # add unique colour markers so the stitcher has keypoints to match
    markers = [
        ((100, 100), (0,   0,   255)),
        ((400, 200), (0,   255, 0  )),
        ((700, 300), (255, 0,   0  )),
        ((1000,150), (255, 255, 0  )),
        ((1300,250), (0,   255, 255)),
        ((1600,100), (255, 0,   255)),
    ]
    for (cx, cy), color in markers:
        cv2.circle(scene, (cx, cy), 30, color, -1)
    return scene


def generate_frames(test_dir: str, n=5, frame_w=960, frame_h=720):
    """Crop n overlapping windows from the scene, save as frame_XXXX.jpg."""
    os.makedirs(test_dir, exist_ok=True)
    scene = make_checkerboard_scene(width=frame_w + (n - 1) * 300, height=frame_h)
    poses = []
    for i in range(n):
        x_offset = i * 300          # 300px overlap between consecutive frames
        crop = scene[:, x_offset:x_offset + frame_w]
        name = f'frame_{i:04d}.jpg'
        cv2.imwrite(os.path.join(test_dir, name), crop)
        # fake pose: 0.5 m steps, altitude 1.5 m
        poses.append({
            'frame': name,
            'timestamp': f'2026-05-25T00:00:0{i}',
            'x': round(i * 0.5, 4),
            'y': 0.0,
            'z': 1.5,
            'yaw': 0.0,
        })
    # write poses.csv
    with open(os.path.join(test_dir, 'poses.csv'), 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=['frame','timestamp','x','y','z','yaw'])
        w.writeheader()
        w.writerows(poses)
    print(f'Generated {n} synthetic frames in {test_dir}')
    return test_dir


# ── run one test ─────────────────────────────────────────────────────────────

def run_test(method: str, test_dir: str) -> bool:
    output = os.path.join(REPO_ROOT, 'data', f'test_mosaic_{method}.png')
    cmd = [
        sys.executable,
        os.path.join(SCRIPTS, 'stitch_mosaic.py'),
        '--images', test_dir,
        '--output', output,
        '--method', method,
    ]
    print(f'\n── Test: method={method} ──────────────────────────')
    result = subprocess.run(cmd, capture_output=False)
    if result.returncode != 0:
        print(f'FAIL  (exit {result.returncode})')
        return False
    if not os.path.isfile(output):
        print(f'FAIL  output file not created: {output}')
        return False
    img = cv2.imread(output)
    if img is None or img.size == 0:
        print(f'FAIL  output image is empty')
        return False
    h, w = img.shape[:2]
    print(f'PASS  → {output}  ({w}×{h} px)')
    return True


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    print('=== stitch_mosaic.py smoke test ===\n')

    # clean up previous test run
    if os.path.isdir(TEST_DIR):
        shutil.rmtree(TEST_DIR)

    generate_frames(TEST_DIR, n=5)

    results = {}
    results['feature'] = run_test('feature', TEST_DIR)
    results['pose']    = run_test('pose',    TEST_DIR)

    print('\n── Summary ───────────────────────────────────────')
    all_pass = True
    for method, passed in results.items():
        status = 'PASS' if passed else 'FAIL'
        print(f'  {method:10s}  {status}')
        if not passed:
            all_pass = False

    print()
    sys.exit(0 if all_pass else 1)


if __name__ == '__main__':
    main()
