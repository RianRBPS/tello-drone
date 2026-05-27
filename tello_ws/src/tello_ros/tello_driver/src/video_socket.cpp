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
  //
  // H264 NAL unit sequencing for a keyframe:
  //   SPS (type 7) → PPS (type 8) → IDR slice (type 5) → P-frames (type 1) …
  //
  // decode_frame() throws H264DecodeFailure when avcodec_decode_video2 returns
  // got_picture==0.  This is NORMAL for SPS and PPS NAL units — the decoder
  // stores the parameter sets internally and produces no picture.  Do NOT call
  // decoder_.flush() on this error: that would wipe the just-stored parameter
  // sets, causing every subsequent IDR/P-frame to fail with
  // "non-existing PPS 0 referenced".
  //
  // Only call decoder_.flush() on genuine packet loss (buffer overflow), which
  // means the current keyframe is unrecoverable and we need to resync on the
  // next IDR.

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
      // Genuine packet loss — the current keyframe is unrecoverable.
      // Flush the decoder so it resyncs on the next IDR instead of
      // trying to conceal errors indefinitely.
      decoder_.flush();
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
      // Parse h264 — returns bytes consumed; sets pkt->size > 0 when a
      // complete NAL unit has been assembled.
      ssize_t consumed = decoder_.parse(seq_buffer_.data() + next, seq_buffer_next_ - next);

      // Safety: if the parser made no progress break to avoid an infinite loop.
      if (consumed <= 0) break;

      // Is a complete H264 packet (NAL unit) available?
      if (decoder_.is_frame_available()) {
        try {
          // Decode the packet.
          // For SPS (type 7) and PPS (type 8) NAL units avcodec_decode_video2
          // stores the parameter sets in the codec context and returns
          // got_picture == 0, which causes decode_frame() to throw
          // H264DecodeFailure.  This is expected and harmless — the SPS/PPS
          // are now stored and will be used when the IDR frame arrives.
          // DO NOT call decoder_.flush() here; that would erase them.
          const AVFrame &frame = decoder_.decode_frame();

          // Skip any malformed frames with no valid dimensions
          if (frame.width <= 0 || frame.height <= 0) {
            next += consumed;
            continue;
          }

          // Convert pixels from YUV420P to BGR24.
          // Heap allocation (std::vector) — avoids a ~2 MB VLA on the thread
          // stack that caused stack overflows at 960×720 resolution.
          int size = converter_.predict_size(frame.width, frame.height);
          std::vector<unsigned char> bgr24(size);
          converter_.convert(frame, bgr24.data());

          // Convert to cv::Mat (no data copy — points into bgr24)
          cv::Mat mat{frame.height, frame.width, CV_8UC3, bgr24.data()};

          // Log the very first successfully decoded frame
          RCLCPP_INFO_ONCE(driver_->get_logger(),
            "First frame decoded: %dx%d — /image_raw is live", frame.width, frame.height);

          auto stamp = driver_->now();

          // Always publish — no subscriber-count gate.
          // The gate could silently drop frames during DDS discovery latency
          // (the first few hundred ms after a subscriber connects).
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
          // got_picture == 0: expected for SPS/PPS NAL units.
          // Log at DEBUG so it doesn't flood the console, and do NOT flush —
          // the parameter sets were stored successfully by the codec.
          RCLCPP_DEBUG(driver_->get_logger(),
            "No picture from this packet (normal for SPS/PPS): %s", e.what());
        }
      }

      next += consumed;
    }
  }

} // namespace tello_driver
