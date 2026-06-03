"""
tello_inspection — main node
============================
Subscribes to the Tello driver topics and implements the indoor inspection
pipeline:

  1. CAPTURE  — save overlapping frames while the drone flies manually
  2. MOSAIC   — stitch saved frames into a single mosaic image (offline)
  3. DETECT   — run defect/anomaly detection on the mosaic (offline, TODO)

Topics consumed (published by tentone/tello-ros2 driver):
  /image_raw      sensor_msgs/Image       — camera frames
  /odom           nav_msgs/Odometry       — position estimate
  /camera_info    sensor_msgs/CameraInfo  — intrinsics

Usage:
  ros2 run tello_inspection tello_inspection

Parameters (set via --ros-args -p name:=value):
  output_dir      str   path to save frames   default: ~/tello-drone/data/images
  trigger_dist_m  float min lateral distance between saved frames (metres)  default: 0.3
  save_all        bool  save every frame (ignore trigger_dist_m)            default: false
"""

import os
import math
import csv
from datetime import datetime

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image, CameraInfo
from nav_msgs.msg import Odometry
from cv_bridge import CvBridge
import cv2


class TelloInspection(Node):

    def __init__(self):
        super().__init__('tello_inspection')

        # ── Parameters ────────────────────────────────────────────────────────
        self.declare_parameter('output_dir',
            os.path.expanduser('~/tello-drone/data/images'))
        self.declare_parameter('trigger_dist_m', 0.3)
        self.declare_parameter('save_all', False)

        self._output_dir    = self.get_parameter('output_dir').value
        self._trigger_dist  = self.get_parameter('trigger_dist_m').value
        self._save_all      = self.get_parameter('save_all').value

        os.makedirs(self._output_dir, exist_ok=True)

        # ── State ─────────────────────────────────────────────────────────────
        self._bridge        = CvBridge()
        self._frame_count   = 0
        self._last_save_pos = None   # (x, y) of last saved frame
        self._last_image    = None   # most recent decoded frame (cv2)
        self._poses_path    = os.path.join(self._output_dir, 'poses.csv')

        # Write CSV header (overwrite on each run)
        with open(self._poses_path, 'w', newline='') as f:
            csv.writer(f).writerow(['frame', 'x', 'y', 'z', 'timestamp'])

        # ── Subscriptions ─────────────────────────────────────────────────────
        self.create_subscription(Image,    'image_raw',    self._on_image,  10)
        self.create_subscription(Odometry, 'odom',         self._on_odom,   10)
        self.create_subscription(CameraInfo,'camera_info', self._on_camera_info, 1)

        self.get_logger().info(
            f'tello_inspection ready — saving frames to {self._output_dir}')
        self.get_logger().info(
            f'trigger_dist={self._trigger_dist} m  save_all={self._save_all}')

    # ── Callbacks ─────────────────────────────────────────────────────────────

    def _on_image(self, msg: Image):
        """Store the latest decoded frame."""
        try:
            self._last_image = self._bridge.imgmsg_to_cv2(msg, 'bgr8')
        except Exception as e:
            self.get_logger().warn(f'Image decode error: {e}')

    def _on_odom(self, msg: Odometry):
        """Decide whether to save a frame based on lateral distance travelled."""
        if self._last_image is None:
            return

        x = msg.pose.pose.position.x
        y = msg.pose.pose.position.y
        z = msg.pose.pose.position.z

        should_save = self._save_all
        if not should_save:
            if self._last_save_pos is None:
                should_save = True
            else:
                dx = x - self._last_save_pos[0]
                dy = y - self._last_save_pos[1]
                if math.sqrt(dx*dx + dy*dy) >= self._trigger_dist:
                    should_save = True

        if should_save:
            self._save_frame(x, y, z, msg.header.stamp)

    def _on_camera_info(self, msg: CameraInfo):
        """Log camera info once so we know calibration arrived."""
        self.get_logger().info(
            f'Camera info received: {msg.width}x{msg.height}',
            once=True)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _save_frame(self, x: float, y: float, z: float, stamp):
        """Save the current frame to disk and log its pose."""
        self._frame_count += 1
        filename = f'frame_{self._frame_count:04d}.jpg'
        filepath = os.path.join(self._output_dir, filename)

        cv2.imwrite(filepath, self._last_image)
        self._last_save_pos = (x, y)

        ts = f'{stamp.sec}.{stamp.nanosec:09d}'
        with open(self._poses_path, 'a', newline='') as f:
            csv.writer(f).writerow([filename, x, y, z, ts])

        self.get_logger().info(
            f'Saved {filename}  pos=({x:.2f}, {y:.2f}, {z:.2f})')


def main():
    rclpy.init()
    node = TelloInspection()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.get_logger().info(
            f'Stopped — {node._frame_count} frames saved to {node._output_dir}')
        node.destroy_node()
        rclpy.shutdown()
