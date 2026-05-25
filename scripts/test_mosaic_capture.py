#!/usr/bin/env python3
"""
test_mosaic_capture.py
======================
Smoke-test for the mosaic_capture ROS 2 node.

Runs the node in-process alongside a fake publisher, publishes synthetic
Image + Odometry messages, then checks that frame JPEGs and poses.csv
are written to disk.

Usage (workspace must be sourced):
    python3 scripts/test_mosaic_capture.py
"""
import os
import sys
import time
import shutil
import threading
import csv

import numpy as np
import rclpy
import rclpy.executors
from rclpy.node import Node
from sensor_msgs.msg import Image
from nav_msgs.msg import Odometry

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEST_OUT  = os.path.join(REPO_ROOT, 'data', 'test_capture_output')


# ── fake publisher ────────────────────────────────────────────────────────────

class FakePublisher(Node):
    def __init__(self):
        super().__init__('fake_publisher')
        self._img_pub  = self.create_publisher(Image,    '/image_raw',    10)
        self._odom_pub = self.create_publisher(Odometry, '/rtabmap/odom', 10)

    def send(self, x: float, y: float):
        """Publish one image then one odom at position (x, y)."""
        self._img_pub.publish(self._make_image(x))
        time.sleep(0.05)
        self._odom_pub.publish(self._make_odom(x, y))

    @staticmethod
    def _make_image(seed: float) -> Image:
        h, w = 240, 320
        arr = np.full((h, w, 3), int(seed * 30) % 255, dtype=np.uint8)
        msg = Image()
        msg.header.frame_id = 'camera'
        msg.height  = h
        msg.width   = w
        msg.encoding = 'bgr8'
        msg.step    = w * 3
        msg.data    = arr.tobytes()
        return msg

    @staticmethod
    def _make_odom(x: float, y: float) -> Odometry:
        msg = Odometry()
        msg.header.frame_id = 'odom'
        msg.pose.pose.position.x = x
        msg.pose.pose.position.y = y
        msg.pose.pose.position.z = 1.5
        msg.pose.pose.orientation.w = 1.0
        return msg


# ── helpers ───────────────────────────────────────────────────────────────────

def add_install_to_path():
    """Add the colcon install tree to sys.path so we can import the node."""
    for pyver in ('python3.10', 'python3.11', 'python3.12'):
        p = os.path.join(REPO_ROOT, 'tello_ws', 'install',
                         'mosaic_capture', 'lib', pyver, 'site-packages')
        if os.path.isdir(p):
            sys.path.insert(0, p)
            return p
    sys.exit('[ERROR] mosaic_capture not built — run: '
             'cd ~/tello-drone/tello_ws && colcon build --packages-select mosaic_capture')


def check_output(out_dir: str, min_frames: int) -> bool:
    frames = sorted(
        f for f in os.listdir(out_dir)
        if f.startswith('frame_') and f.endswith('.jpg')
    )
    csv_path = os.path.join(out_dir, 'poses.csv')
    csv_ok   = os.path.isfile(csv_path)

    print(f'  frames saved : {len(frames)}  (expected ≥ {min_frames})')
    print(f'  poses.csv    : {"found" if csv_ok else "MISSING"}')
    if frames:
        print(f'  files        : {", ".join(frames[:8])}')
    if csv_ok:
        with open(csv_path) as f:
            rows = list(csv.DictReader(f))
        print(f'  csv rows     : {len(rows)}')

    return len(frames) >= min_frames and csv_ok


# ── main test ─────────────────────────────────────────────────────────────────

def main():
    print('=== mosaic_capture smoke test ===\n')

    add_install_to_path()
    from mosaic_capture.node import MosaicCapture  # noqa: E402

    # clean output dir
    if os.path.isdir(TEST_OUT):
        shutil.rmtree(TEST_OUT)
    os.makedirs(TEST_OUT)

    # Pass parameters via rclpy args — picked up by declare_parameter() in node
    rclpy.init(args=[
        '--ros-args',
        '-p', f'output_dir:={TEST_OUT}',
        '-p', 'trigger_dist:=0.5',
    ])

    capture = MosaicCapture()

    pub = FakePublisher()

    executor = rclpy.executors.MultiThreadedExecutor()
    executor.add_node(capture)
    executor.add_node(pub)

    spin_thread = threading.Thread(target=executor.spin, daemon=True)
    spin_thread.start()

    # Allow subscriptions to connect
    time.sleep(0.5)

    # Publish 6 positions 0.6 m apart — should trigger 5 saves
    # (first position sets baseline; each subsequent one exceeds 0.5 m)
    print('Publishing 6 positions @ 0.6 m spacing...')
    positions = [(i * 0.6, 0.0) for i in range(6)]
    for x, y in positions:
        pub.send(x, y)
        time.sleep(0.2)

    # Let final callbacks settle
    time.sleep(0.5)
    executor.shutdown(timeout_sec=2.0)
    capture.destroy_node()
    pub.destroy_node()
    rclpy.shutdown()

    print()
    passed = check_output(TEST_OUT, min_frames=5)
    print(f'\n{"PASS" if passed else "FAIL"}')
    sys.exit(0 if passed else 1)


if __name__ == '__main__':
    main()
