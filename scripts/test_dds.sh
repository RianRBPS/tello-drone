#!/usr/bin/env bash
# DDS large-message test for WSL2 — NO DRONE NEEDED.
#
# Reproduces the Session 9 blocker (/image_raw ~2 MB frames never cross
# process boundaries on WSL2 while small messages work) and tests whether
# FastDDS or the compressed pipeline fixes it.
#
# Usage (inside WSL, from the repo root):
#   bash scripts/test_dds.sh fastdds      # candidate fix (shared-memory transport)
#   bash scripts/test_dds.sh cyclonedds   # current config (expected: image_raw FAILS)
#
# Interpreting results:
#   /ping OK, /image_raw OK               -> DDS fixed, use this RMW for everything
#   /ping OK, /image_raw FAIL, /compressed OK -> use the compressed pipeline
#   /ping FAIL                            -> daemon/env problem, not a size problem

# note: no `set -u` — ROS setup.bash references unset vars and would abort

RMW_CHOICE="${1:-fastdds}"
case "$RMW_CHOICE" in
  fastdds)
    export RMW_IMPLEMENTATION=rmw_fastrtps_cpp
    unset CYCLONEDDS_URI
    ;;
  cyclonedds)
    export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
    ;;
  *)
    echo "usage: $0 fastdds|cyclonedds"
    exit 1
    ;;
esac

source /opt/ros/humble/setup.bash

echo "=== RMW: $RMW_IMPLEMENTATION ==="

# Session ritual: kill stale daemon (it caches the RMW it was started with)
pkill -9 -f "ros2 daemon" 2>/dev/null
sleep 2
ros2 daemon start
sleep 1

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
python3 "$SCRIPT_DIR/fake_image_pub.py" &
PUB_PID=$!
trap 'kill $PUB_PID 2>/dev/null' EXIT
sleep 3

PASS=()
FAIL=()

check_topic() {
  local topic=$1
  echo ""
  echo "--- ros2 topic hz $topic (10 s) ---"
  local out
  out=$(timeout 10 ros2 topic hz "$topic" --window 20 2>&1)
  echo "$out" | grep "average rate" | tail -1
  if echo "$out" | grep -q "average rate"; then
    PASS+=("$topic")
  else
    echo "(no messages received)"
    FAIL+=("$topic")
  fi
}

check_topic /ping
check_topic /image_raw
check_topic /image_raw/compressed

echo ""
echo "==================== SUMMARY ($RMW_CHOICE) ===================="
for t in "${PASS[@]:-}"; do [ -n "$t" ] && echo "  PASS  $t"; done
for t in "${FAIL[@]:-}"; do [ -n "$t" ] && echo "  FAIL  $t"; done
echo "==============================================================="
