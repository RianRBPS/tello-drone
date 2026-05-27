#include "tello_driver_node.hpp"

#include <libavutil/frame.h>
#include <opencv2/imgproc.hpp>

#include "camera_calibration_parsers/parse.hpp"

namespace tello_driver
{

  // Notes on Tello video:
  // -- frames are always 960x720.
  // -- frames are split into UDP packets of length 1460.
  // -- normal frames are ~10k, or about 8 UDP packets.
  // -- keyframes are ~35k, or about 25 UDP packets.
  // -- keyframes are always preceded by an 8-byte UDP packet and a 13-byte UDP packet -- markers?
  // -- the h264 parser will consume the 8-byte packet, the 13-byte packet and the entire keyframe without
  //    generating a frame. Presumably the keyframe is stored in the parser and referenced later.

  VideoSocket::VideoSocket(TelloDriverNode *driver, unsigned short video_port, const std::string &camera_info_path) :
    TelloSocket(driver, video_port)
  {
    std::string camera_name;
    if (camera_calibration_parsers::readCalibration(camera_info_path, camera_name, camera_info_msg_)) {
      RCLCPP_INFO(driver_->get_logger(), "Parsed camera info for '%s'", camera_name.c_str());
    } else {
      RCLCPP_ERROR(driver_->get_logger(), "Cannot get camera info");
    }

    buffer_ = std::vector<unsigned char>(2048);
    seq_buffer_ = std::vector<unsigned char>(65536);
    listen();
  }

  // Process a video packet from the drone
  void VideoSocket::process_packet(size_t r)
  {
    std::lock_guard<std::mutex> lock(mtx_);

    receive_time_ = driver_->now();

    if (!receiving_) {
      // First packet
      RCLCPP_INFO(driver_->get_logger(), "Receiving video");
      receiving_ = true;
      seq_buffer_next_ = 0;
      seq_buffer_num_packets_ = 0;
    }

    if (seq_buffer_next_ + r >= seq_buffer_.size()) {
      RCLCPP_ERROR(driver_->get_logger(), "Video buffer overflow, dropping sequence");
      decoder_.flush();  // discard reference frames — resync on next IDR
      seq_buffer_next_ = 0;
      seq_buffer_num_packets_ = 0;
      return;
    }

    std::copy(buffer_.begin(), buffer_.begin() + r, seq_buffer_.begin() + seq_buffer_next_);
    seq_buffer_next_ += r;
    seq_buffer_num_packets_++;

    // If the packet is < 1460 bytes then it's the last packet in the sequence
    if (r < 1460) {
      decode_frames();

      seq_buffer_next_ = 0;
      seq_buffer_num_packets_ = 0;
    }
  }

  // Decode frames
  void VideoSocket::decode_frames()
  {
    size_t next = 0;

    while (next < seq_buffer_next_) {
      // Parse h264 — returns bytes consumed; may be 0 if parser needs more data
      ssize_t consumed = decoder_.parse(seq_buffer_.data() + next, seq_buffer_next_ - next);

      // Safety: if the parser made no progress break to avoid an infinite loop
      if (consumed <= 0) break;

      // Is a complete H264 packet (NAL unit / access unit) available?
      if (decoder_.is_frame_available()) {
        try {
          // Decode the frame — throws H264DecodeFailure if got_picture == 0
          // (legitimate for SPS/PPS NAL units or severely corrupted data)
          const AVFrame &frame = decoder_.decode_frame();

          // Skip any malformed frames with no valid dimensions
          if (frame.width <= 0 || frame.height <= 0) {
            next += consumed;
            continue;
          }

          // Convert pixels from YUV420P to BGR24.
          // Use heap allocation (std::vector) — the VLA equivalent
          // (unsigned char bgr24[size]) was ~2 MB on the thread stack, a
          // stack-overflow risk for a 960×720 frame.
          int size = converter_.predict_size(frame.width, frame.height);
          std::vector<unsigned char> bgr24(size);
          converter_.convert(frame, bgr24.data());

          // Convert to cv::Mat (no data copy — points into bgr24)
          cv::Mat mat{frame.height, frame.width, CV_8UC3, bgr24.data()};

          // Log the very first successfully decoded frame so we know decoding works
          RCLCPP_INFO_ONCE(driver_->get_logger(),
            "First frame decoded: %dx%d — publishing on /image_raw", frame.width, frame.height);

          // Synchronize ROS message timestamps
          auto stamp = driver_->now();

          // Always publish — don't gate on subscriber count.
          // The subscriber-count check can silently drop frames during DDS
          // discovery (the first few hundred milliseconds after a new
          // subscriber attaches).  The bandwidth cost on loopback is fine.
          {
            std_msgs::msg::Header header{};
            header.frame_id = "camera_frame";
            header.stamp = stamp;
            cv_bridge::CvImage cv_image{header, sensor_msgs::image_encodings::BGR8, mat};
            sensor_msgs::msg::Image sensor_image_msg;
            cv_image.toImageMsg(sensor_image_msg);
            driver_->image_pub_->publish(sensor_image_msg);
          }

          if (driver_->count_subscribers(driver_->camera_info_pub_->get_topic_name()) > 0) {
            camera_info_msg_.header.stamp = stamp;
            driver_->camera_info_pub_->publish(camera_info_msg_);
          }

        } catch (std::runtime_error &e) {
          // decode_frame() threw — either got_picture==0 (SPS/PPS, not a real
          // frame) or a truly corrupted frame.  Flush the decoder so it stops
          // trying to conceal errors against a broken reference frame and
          // resyncs cleanly on the next IDR keyframe.
          RCLCPP_WARN_THROTTLE(driver_->get_logger(), *driver_->get_clock(), 2000,
            "Frame decode failed, resyncing decoder: %s", e.what());
          decoder_.flush();
        }
      }

      next += consumed;
    }
  }

} // namespace tello_driver
