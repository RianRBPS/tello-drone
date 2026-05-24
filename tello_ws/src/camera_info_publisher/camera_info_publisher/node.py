import rclpy
from rclpy.node import Node
from sensor_msgs.msg import CameraInfo, Image
import yaml
import os


class CameraInfoPublisher(Node):
    def __init__(self):
        super().__init__('camera_info_publisher')
        self.declare_parameter('calibration_file', '')

        cal_file = self.get_parameter('calibration_file').get_parameter_value().string_value
        if not cal_file or not os.path.isfile(cal_file):
            self.get_logger().fatal(f'calibration_file not found: {cal_file}')
            raise SystemExit(1)

        with open(cal_file) as f:
            cal = yaml.safe_load(f)

        self._msg = CameraInfo()
        self._msg.width = cal['image_width']
        self._msg.height = cal['image_height']
        self._msg.distortion_model = cal['distortion_model']
        self._msg.d = cal['distortion_coefficients']['data']
        self._msg.k = cal['camera_matrix']['data']
        self._msg.r = cal['rectification_matrix']['data']
        self._msg.p = cal['projection_matrix']['data']

        self._pub = self.create_publisher(CameraInfo, '/camera_info', 10)
        self._sub = self.create_subscription(Image, '/image_raw', self._on_image, 10)
        self.get_logger().info(f'Publishing /camera_info from {cal_file}')

    def _on_image(self, img_msg: Image):
        self._msg.header = img_msg.header
        self._pub.publish(self._msg)


def main():
    rclpy.init()
    node = CameraInfoPublisher()
    rclpy.spin(node)
    rclpy.shutdown()
