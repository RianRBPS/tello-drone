#!/usr/bin/env python3
"""Fake Tello camera publisher — reproduces the WSL2 DDS large-message issue
WITHOUT the drone.

Publishes (same names and QoS as the tentone driver):
  /image_raw             sensor_msgs/Image      960x720 bgr8 (~2 MB/frame), 15 Hz
  /image_raw/compressed  sensor_msgs/CompressedImage  JPEG (~30-50 KB), 15 Hz
  /ping                  std_msgs/String        tiny control message, 15 Hz

If /ping crosses to another terminal but /image_raw does not, the problem is
the DDS transport with large messages (the Session 9 blocker). If
/image_raw/compressed crosses, the compressed pipeline is a valid workaround.

Run:  python3 scripts/fake_image_pub.py
Test: ros2 topic hz /image_raw          (in a second terminal)
      or use scripts/test_dds.sh which automates both sides.
"""

import numpy as np
import cv2
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, QoSReliabilityPolicy, QoSHistoryPolicy
from sensor_msgs.msg import Image, CompressedImage
from std_msgs.msg import String

WIDTH, HEIGHT, FPS = 960, 720, 15

# Same QoS as the driver's image publisher
IMAGE_QOS = QoSProfile(
    reliability=QoSReliabilityPolicy.BEST_EFFORT,
    history=QoSHistoryPolicy.KEEP_LAST,
    depth=1,
)


class FakeImagePub(Node):
    def __init__(self):
        super().__init__('fake_image_pub')
        self.pub_raw = self.create_publisher(Image, 'image_raw', IMAGE_QOS)
        self.pub_comp = self.create_publisher(
            CompressedImage, 'image_raw/compressed', IMAGE_QOS)
        self.pub_ping = self.create_publisher(String, 'ping', 10)
        self.count = 0
        self.create_timer(1.0 / FPS, self.tick)
        self.get_logger().info(
            f'Publishing fake {WIDTH}x{HEIGHT} bgr8 at {FPS} Hz '
            f'(~{WIDTH * HEIGHT * 3 // 1024} KB per raw frame)')

    def tick(self):
        # Moving block + frame counter so motion is visible in rqt_image_view
        frame = np.zeros((HEIGHT, WIDTH, 3), np.uint8)
        x = (self.count * 8) % (WIDTH - 80)
        cv2.rectangle(frame, (x, 300), (x + 80, 420), (0, 255, 0), -1)
        cv2.putText(frame, f'frame {self.count}', (40, 80),
                    cv2.FONT_HERSHEY_SIMPLEX, 2, (255, 255, 255), 3)

        stamp = self.get_clock().now().to_msg()

        msg = Image()
        msg.header.stamp = stamp
        msg.header.frame_id = 'drone'
        msg.height, msg.width = HEIGHT, WIDTH
        msg.encoding = 'bgr8'
        msg.step = WIDTH * 3
        msg.data = frame.tobytes()
        self.pub_raw.publish(msg)

        ok, jpeg = cv2.imencode('.jpg', frame,
                                [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        if ok:
            cmsg = CompressedImage()
            cmsg.header = msg.header
            cmsg.format = 'jpeg'
            cmsg.data = jpeg.tobytes()
            self.pub_comp.publish(cmsg)

        ping = String()
        ping.data = f'ping {self.count}'
        self.pub_ping.publish(ping)

        if self.count % (FPS * 5) == 0:
            self.get_logger().info(f'Published {self.count} frames')
        self.count += 1


def main():
    rclpy.init()
    node = FakeImagePub()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    node.destroy_node()
    rclpy.shutdown()


if __name__ == '__main__':
    main()
