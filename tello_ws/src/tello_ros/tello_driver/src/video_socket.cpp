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
  // -- keyframes are always preceded by an 8-byte UDP packet and a 13-byte UDP packet.
  //    These are the SPS (type 7) and PPS (type 8) NAL units sent as small stand-alone
  //    UDP packets, each of which triggers an immediate decode_frames() call.
  // -- the h264 parser will consume the 8-byte packet and the 13-byte packet without
  //    generating a packet (pkt->size == 0), storing the NAL units in its internal
  //    buffer.  When the NEXT NAL start-code arrives (in the IDR sequence), the parser
  //    flushes the buffered NAL as an output packet with consumed == 0.
  //
  // Critical decode-loop ordering rule:
  //   av_parser_parse2 can return consumed == 0 when it flushes a buffered packet
  //   (e.g. SPS or PPS) without consuming any new input bytes.
  //   The "safety break on consumed <= 0" MUST come AFTER the is_frame_available()
  //   check, not before — otherwise the flushed SPS/PPS packet is silently discarded
  //   and the decoder context never gets the parameter sets it needs to decode IDR/P.

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
      // Flush the decoder so it resyncs on the next IDR.
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
      // Parse one NAL unit from the buffer.
      // consumed == 0 is valid: the parser can flush a previously buffered
      // packet (SPS or PPS) without consuming any new input bytes.
      ssize_t consumed = decoder_.parse(seq_buffer_.data() + next, seq_buffer_next_ - next);

      // Process any available packet BEFORE checking consumed.
      // If we check consumed <= 0 first and break, a buffered SPS/PPS packet
      // that was just flushed (with consumed == 0) would be silently dropped,
      // leaving the decoder without the parameter sets it needs for IDR/P frames.
      if (decoder_.is_frame_available()) {
        try {
          // Decode the packet.
          // SPS (type 7) and PPS (type 8) NAL units return got_picture == 0,
          // causing decode_frame() to throw H264DecodeFailure.  This is
          // expected and correct — the codec stores the parameter sets and
          // produces no display picture.  Do NOT flush on this error.
          const AVFrame &frame = decoder_.decode_frame();

          // Skip malformed frames with no valid dimensions
          if (frame.width <= 0 || frame.height <= 0) {
            // Not a displayable frame (likely a pure parameter-set packet
            // that somehow returned got_picture==1).  Advance and continue.
            if (consumed <= 0) break;
            next += consumed;
            continue;
          }

          // Convert YUV420P → BGR24 using heap allocation.
          // The original VLA "unsigned char bgr24[size]" put ~2 MB on the
          // thread stack at 960×720, risking a stack overflow.
          int size = converter_.predict_size(frame.width, frame.height);
          std::vector<unsigned char> bgr24(size);
          converter_.convert(frame, bgr24.data());

          // cv::Mat wraps bgr24 without copying
          cv::Mat mat{frame.height, frame.width, CV_8UC3, bgr24.data()};

          RCLCPP_INFO_ONCE(driver_->get_logger(),
            "First frame decoded: %dx%d — /image_raw is live", frame.width, frame.height);

          auto stamp = driver_->now();

          // Rate-limit publishing to kMaxPublishHz (default 15 fps).
          // The Tello sends 30 fps but 15 fps is plenty for mosaic capture
          // and halves the loopback bandwidth + CPU load on WSL2.
          if (kMaxPublishHz > 0.0) {
            double elapsed = (stamp - last_frame_published_).seconds();
            if (elapsed < 1.0 / kMaxPublishHz) {
              if (consumed <= 0) break;
              next += consumed;
              continue;
            }
          }
          last_frame_published_ = stamp;

          // Always publish (no subscriber-count gate — DDS discovery latency
          // can make count_subscribers() return 0 for the first few hundred ms).
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
          // got_picture == 0: normal for SPS/PPS NAL units.
          // The parameter sets were stored in the codec — do NOT flush.
          RCLCPP_DEBUG(driver_->get_logger(),
            "No picture from packet (SPS/PPS or partial): %s", e.what());
        }
      }

      // Safety: if the parser made no progress AND has no buffered output,
      // break to prevent an infinite loop.  This check must come AFTER the
      // is_frame_available() block above.
      if (consumed <= 0) break;
      next += consumed;
    }
  }

} // namespace tello_driver
