# Source this at the START of EVERY WSL terminal, old or new — it repairs both:
#
#   source ~/tello-drone/scripts/ros_env.sh
#
# It guarantees all terminals share the same DDS environment (session 10:
# default FastDDS, localhost only). Mixed environments = nodes can't see
# each other = "Waiting for at least 1 matching subscription(s)..." forever.

unset RMW_IMPLEMENTATION CYCLONEDDS_URI
export ROS_LOCALHOST_ONLY=1
source /opt/ros/humble/setup.bash
source ~/tello-drone/tello_ws/install/setup.bash
echo "[ros_env] OK — FastDDS (default RMW), ROS_LOCALHOST_ONLY=1"
