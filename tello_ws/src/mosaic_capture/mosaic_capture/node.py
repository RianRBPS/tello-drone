"""
mosaic_capture node
====================
Subscribes to /image_raw/compressed and /odom.
Saves a JPEG frame + pose row to data/images/ on a periodic timer
(trigger_period) and/or every trigger_dist metres of lateral travel.
Also listens for a manual trigger on /mosaic_capture/trigger.

Works live with the drone OR offline against a bag:
    ros2 bag play ~/tello-drone/data/bags/voo_08 --loop

Parameters
----------
output_dir     : str   path to save images (default: <repo>/data/images)
image_topic    : str   CompressedImage topic (default: /image_raw/compressed)
odom_topic     : str   Odometry topic (default: /odom)
trigger_period : float seconds between captures, 0 disables (default: 2.0)
trigger_dist   : float metres between captures, 0 disables (default: 0.5)
                 NOTE: the tentone driver's /odom carries no position (always
                 0,0,0), so the distance trigger only fires with real
                 odometry (e.g. rtabmap). The time trigger is the default.
"""

import os
import math
import csv
from datetime import datetime

import numpy as np

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy

from sensor_msgs.msg import CompressedImage
from nav_msgs.msg import Odometry
from std_msgs.msg import Empty

import cv2


class MosaicCapture(Node):
    def __init__(self):
        super().__init__('mosaic_capture')

        self.declare_parameter('output_dir', '')
        self.declare_parameter('image_topic', '/image_raw/compressed')
        self.declare_parameter('odom_topic', '/odom')
        self.declare_parameter('trigger_period', 2.0)
        self.declare_parameter('trigger_dist', 0.5)

        out_dir = self.get_parameter('output_dir').get_parameter_value().string_value
        if not out_dir:
            # Walk up from this file until we find PLAN.md (repo root marker)
            here = os.path.dirname(os.path.abspath(__file__))
            repo_root = here
            for _ in range(12):
                if os.path.isfile(os.path.join(repo_root, 'PLAN.md')):
                    break
                repo_root = os.path.dirname(repo_root)
            out_dir = os.path.join(repo_root, 'data', 'images')
        self._out_dir = os.path.realpath(out_dir)
        os.makedirs(self._out_dir, exist_ok=True)

        image_topic = self.get_parameter('image_topic').get_parameter_value().string_value
        odom_topic = self.get_parameter('odom_topic').get_parameter_value().string_value
        self._trigger_period: float = (
            self.get_parameter('trigger_period').get_parameter_value().double_value
        )
        self._trigger_dist: float = (
            self.get_parameter('trigger_dist').get_parameter_value().double_value
        )

        self._latest_image: CompressedImage | None = None
        self._latest_odom: Odometry | None = None
        self._last_x: float | None = None
        self._last_y: float | None = None
        self._frame_idx: int = 0

        # CSV pose log
        self._csv_path = os.path.join(self._out_dir, 'poses.csv')
        self._csv_file = open(self._csv_path, 'a', newline='')
        self._csv_writer = csv.writer(self._csv_file)
        if os.path.getsize(self._csv_path) == 0:
            self._csv_writer.writerow(['frame', 'timestamp', 'x', 'y', 'z', 'yaw'])

        best_effort = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=1,
        )

        self.create_subscription(CompressedImage, image_topic, self._on_image, best_effort)
        self.create_subscription(Odometry, odom_topic, self._on_odom, 10)
        self.create_subscription(Empty, '/mosaic_capture/trigger', self._on_trigger, 10)

        if self._trigger_period > 0:
            self.create_timer(self._trigger_period, self._on_timer)

        self.get_logger().info(
            f'mosaic_capture ready | output={self._out_dir} | image={image_topic} | '
            f'odom={odom_topic} | period={self._trigger_period} s | dist={self._trigger_dist} m'
        )

    # ------------------------------------------------------------------
    def _on_image(self, msg: CompressedImage):
        self._latest_image = msg

    def _on_odom(self, msg: Odometry):
        self._latest_odom = msg

        if self._trigger_dist <= 0:
            return

        x = msg.pose.pose.position.x
        y = msg.pose.pose.position.y

        if self._last_x is None:
            self._last_x, self._last_y = x, y
            return

        dist = math.hypot(x - self._last_x, y - self._last_y)
        if dist >= self._trigger_dist:
            self._save_frame(msg)
            self._last_x, self._last_y = x, y

    def _on_timer(self):
        self._save_frame(self._latest_odom)

    def _on_trigger(self, _msg: Empty):
        """Manual capture via: ros2 topic pub /mosaic_capture/trigger std_msgs/Empty '{}'"""
        self.get_logger().info('Manual trigger received')
        self._save_frame(self._latest_odom)

    # ------------------------------------------------------------------
    def _save_frame(self, odom_msg: Odometry | None):
        if self._latest_image is None:
            self.get_logger().warn('No image received yet — skipping frame',
                                   throttle_duration_sec=5.0)
            return

        try:
            buf = np.frombuffer(self._latest_image.data, dtype=np.uint8)
            cv_img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
            if cv_img is None:
                raise ValueError('cv2.imdecode returned None')
        except Exception as e:
            self.get_logger().error(f'JPEG decode error: {e}')
            return

        name = f'frame_{self._frame_idx:04d}.jpg'
        path = os.path.join(self._out_dir, name)
        cv2.imwrite(path, cv_img)

        ts = datetime.now().isoformat()
        if odom_msg is not None:
            p = odom_msg.pose.pose.position
            q = odom_msg.pose.pose.orientation
            yaw = _quat_to_yaw(q.x, q.y, q.z, q.w)
            self._csv_writer.writerow([name, ts, round(p.x, 4), round(p.y, 4), round(p.z, 4), round(yaw, 4)])
        else:
            self._csv_writer.writerow([name, ts, 'none', 'none', 'none', 'none'])
        self._csv_file.flush()

        self.get_logger().info(f'Saved {name}  (total: {self._frame_idx + 1})')
        self._frame_idx += 1

    def destroy_node(self):
        self._csv_file.close()
        super().destroy_node()


# ------------------------------------------------------------------
def _quat_to_yaw(qx, qy, qz, qw) -> float:
    siny_cosp = 2.0 * (qw * qz + qx * qy)
    cosy_cosp = 1.0 - 2.0 * (qy * qy + qz * qz)
    return math.atan2(siny_cosp, cosy_cosp)


def main():
    rclpy.init()
    node = MosaicCapture()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()
