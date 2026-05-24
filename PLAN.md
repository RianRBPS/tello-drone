# Tello Drone — ROS Autonomous Flight & Mosaic Mapping
**Platform:** DJI Tello | ROS 2 Humble | WSL2 on Windows 11

---

## Project Goals

| # | Goal | CPU-only? | Phase |
|---|------|-----------|-------|
| 1 | Fly and control Tello via ROS 2 on WSL2 | yes | 1 |
| 2 | Visualize drone state and camera in RViz | yes | 1–2 |
| 3 | Camera calibration + visual odometry | yes | 2 |
| 4 | Take photos and stitch a mosaic map | yes | 3 |
| 5 | Basic autonomous grid mission | yes | 4 |
| 6 | Detect distances (monocular depth) | partial | 5 |
| 7 | Collision avoidance | needs GPU for real-time | 6 |

---

## Modularity Principles

This project is built around **swappable layers**. Each boundary below is an interface, not a hard dependency:

| Layer | Current | Future swap-in |
|-------|---------|---------------|
| **Drone hardware** | DJI Tello (WiFi UDP) | Any MAVLink drone, DJI SDK, PX4 SITL sim |
| **Connectivity** | Notebook WiFi → Tello AP | USB dongle, Tello EDU station mode, Ethernet |
| **Depth estimator** | Depth Anything V2 Small (CPU/ONNX) | Larger model on GPU, stereo camera, ToF sensor |
| **Odometry** | rtabmap visual odometry | ORB-SLAM3, wheel odometry, external VICON |
| **Mission planner** | Custom lightweight waypoint node | nav2, MAVSDK, BehaviorTree.CPP |
| **Map output** | OpenCV mosaic stitch | ROS OccupancyGrid, GeoTIFF, Mapbox |

**Rule:** custom nodes talk only to ROS topics/services. No node imports `tello_ros` directly. Swapping the drone = swap the driver + launch file, nothing else.

---

## Architecture Overview

```
Windows 11 (notebook WiFi → Tello AP)
└── WSL2 (Ubuntu 22.04) ── networkingMode=mirrored
    └── ROS 2 Humble
        │
        ├── [LAYER 1 — DRONE INTERFACE]
        │   └── drone_driver node        topic contract: /image_raw, /imu,
        │       (currently: tello_ros)                   /flight_data, /cmd_vel
        │
        ├── [LAYER 2 — STATE ESTIMATION]   ← CPU-friendly
        │   ├── camera_info_publisher
        │   └── rtabmap (visual odometry + map)
        │
        ├── [LAYER 3 — MISSION & CAPTURE]  ← CPU-friendly
        │   ├── mission_planner node
        │   └── mosaic_capture node
        │
        ├── [LAYER 4 — PERCEPTION]         ← GPU-accelerated later
        │   ├── depth_estimator node       (Depth Anything V2 Small now)
        │   └── obstacle_avoidance node
        │
        └── RViz2 (WSLg)
```

---

## Phase 0 — Environment Setup

### 0.1 WSL2 — Mirrored Networking

Required so WSL2 can reach the Tello's WiFi AP through the Windows adapter.

- [ ] Create/edit `%USERPROFILE%\.wslconfig`:

```ini
[wsl2]
networkingMode=mirrored
```

- [ ] `wsl --shutdown` then reopen WSL
- [ ] Verify: `ping 192.168.10.1` (connect to Tello AP first from Windows)

> **Future swap:** When you add a USB dongle or switch to Tello EDU station mode,
> this setting stays the same — WSL2 sees all adapters automatically.

### 0.2 Display — RViz via WSLg

Windows 11 includes WSLg out of the box.

- [ ] Open WSL and run: `echo $DISPLAY` — should return `:0`
- [ ] Test: `sudo apt install -y x11-apps && xclock`
- [ ] Fallback (if WSLg missing): install VcXsrv on Windows, then:
  ```bash
  export DISPLAY=$(cat /etc/resolv.conf | grep nameserver | awk '{print $2}'):0.0
  ```

### 0.3 ROS 2 Humble

```bash
sudo apt update && sudo apt install -y locales && sudo locale-gen en_US en_US.UTF-8

sudo apt install -y software-properties-common curl
sudo curl -sSL https://raw.githubusercontent.com/ros/rosdistro/master/ros.key \
  -o /usr/share/keyrings/ros-archive-keyring.gpg
echo "deb [arch=$(dpkg --print-architecture) \
  signed-by=/usr/share/keyrings/ros-archive-keyring.gpg] \
  http://packages.ros.org/ros2/ubuntu $(. /etc/os-release && echo $UBUNTU_CODENAME) main" \
  | sudo tee /etc/apt/sources.list.d/ros2.list

sudo apt update
sudo apt install -y ros-humble-desktop python3-colcon-common-extensions python3-rosdep
sudo rosdep init && rosdep update

echo "source /opt/ros/humble/setup.bash" >> ~/.bashrc
source ~/.bashrc
```

---

## Phase 1 — Drone Interface & Basic Control  `CPU` `EARLY`

### 1.1 Workspace & Driver

```bash
mkdir -p ~/tello_ws/src && cd ~/tello_ws/src
git clone https://github.com/clydemcqueen/tello_ros.git
git clone https://github.com/ptrmu/ros2_shared.git

cd ~/tello_ws
rosdep install --from-paths src --ignore-src -r -y
colcon build && source install/setup.bash
```

**Published topics (the interface contract — never depend on driver internals):**

| Topic | Type | Notes |
|-------|------|-------|
| `/image_raw` | `sensor_msgs/Image` | 30 fps, 960×720 |
| `/imu` | `sensor_msgs/Imu` | |
| `/flight_data` | `tello_msgs/FlightData` | battery, barometer, speed |
| `/cmd_vel` | `geometry_msgs/Twist` | velocity control input |

### 1.2 First Flight Test

```bash
# Terminal 1
ros2 launch tello_ros tello_driver_launch.py

# Terminal 2 — takeoff / land
ros2 service call /tello_action tello_msgs/TelloAction "{cmd: 'takeoff'}"
ros2 service call /tello_action tello_msgs/TelloAction "{cmd: 'land'}"
```

### 1.3 RViz2 First Look

- [ ] `ros2 run rviz2 rviz2`
- [ ] Add `Image` display → `/image_raw`
- [ ] Add `TF` display
- [ ] Save config → `config/tello.rviz`

---

## Phase 2 — State Estimation  `CPU` `EARLY`

Tello has no GPS. Pose is built from on-board sensors + visual odometry.

| Source | Method | CPU cost |
|--------|--------|----------|
| IMU | from `tello_ros` directly | negligible |
| Altitude | barometer in `flight_data` | negligible |
| Lateral pose | rtabmap visual odometry | medium — tunable |
| Downward drift | optical flow (downward cam) | low |

### 2.1 Camera Calibration (do this first)

```bash
sudo apt install -y ros-humble-camera-calibration
ros2 run camera_calibration cameracalibrator \
  --size 8x6 --square 0.025 \
  image:=/image_raw camera:=/camera
```

Save output as `config/calibration.yaml`.

### 2.2 rtabmap Visual Odometry — CPU-Tuned

```bash
sudo apt install -y ros-humble-rtabmap-ros
```

```bash
ros2 launch rtabmap_launch rtabmap.launch.py \
  visual_odometry:=true \
  rgb_topic:=/image_raw \
  camera_info_topic:=/camera_info \
  frame_id:=base_link \
  Vis/MaxFeatures:=500 \
  Kp/MaxFeatures:=500
```

> The `MaxFeatures` params halve CPU load with minimal odometry quality loss.
> When GPU is available these can be raised to 1000+ for better drift correction.

### 2.3 RViz Odometry Display

- [ ] Add `Odometry` display → `/rtabmap/odom`
- [ ] Add `Map` display → `/rtabmap/map`

---

## Phase 3 — Mosaic Mapping  `CPU` `EARLY`

Pure OpenCV — no neural network, runs easily on CPU.

### 3.1 Image Capture Node

```
subscribe  /image_raw
subscribe  /rtabmap/odom

every N meters of lateral travel:
    save frame_XXXX.jpg + pose row to poses.csv
```

- [ ] Create `mosaic_capture` ROS 2 package
- [ ] Configurable trigger distance (default 0.5 m)
- [ ] Save to `data/images/`

### 3.2 Offline Mosaic Stitching

```bash
pip install opencv-python numpy
```

`scripts/stitch_mosaic.py` outline:
```python
# Option A — pose-guided homography (uses saved poses.csv)
# 1. Load images + poses
# 2. Compute homographies from consecutive poses (rotation + altitude scale)
# 3. Warp + blend onto canvas → final_mosaic.png

# Option B — feature-based (no pose needed, pure OpenCV)
stitcher = cv2.Stitcher_create(cv2.Stitcher_SCANS)
status, mosaic = stitcher.stitch(images)
```

Start with Option B; upgrade to A once odometry is reliable.

### 3.3 Geo-Reference (Optional Later)

If flying outdoors with a known start coordinate:
- Derive pixel/meter scale from barometer altitude
- Export GeoTIFF via GDAL

---

## Phase 4 — Basic Autonomous Mission  `CPU` `EARLY`

Grid waypoint flight without obstacle avoidance (avoidance is Phase 6).
Fly slow, stay conservative.

### 4.1 Mission Planner Node

```
takeoff
for each (x, y) in grid:
    fly_to(x, y, altitude)    ← PD controller on /rtabmap/odom error
    wait for pose settled
    trigger mosaic_capture
land
```

- [ ] Create `mission_planner` ROS 2 package
- [ ] Configurable: grid size, step size, altitude, capture trigger distance
- [ ] Emergency land on `/battery` < 20%
- [ ] Publishes `/mission_status` so RViz can show progress

### 4.2 Altitude Hold

- PID loop on barometer altitude from `flight_data`
- Correction injected into `cmd_vel.linear.z`

> **Modularity note:** The mission planner sends only `geometry_msgs/Twist` on
> `/cmd_vel`. Phase 6's avoidance node will sit between planner and driver,
> intercepting and modifying velocity commands — no changes to the planner needed.

---

## Phase 5 — Distance Estimation  `CPU (limited)` `LATER`

> Start this phase once Phases 1–4 are working end-to-end.

### 5.1 Depth Estimator — CPU Stack

| Model | Backbone | ~CPU fps @ 320×240 | Notes |
|-------|----------|--------------------|-------|
| Depth Anything V2 Small | ViT-S | 4–6 fps | **Use this** |
| MiDaS Small | MobileNetV2 | 6–10 fps | Fallback |
| MiDaS Large | ResNeXt-101 | < 1 fps | Avoid on CPU |

```bash
pip install torch torchvision onnxruntime depth-anything-v2
```

Node pipeline:
```
/image_raw (30 Hz)
  → downsample 320×240
  → Depth Anything V2 Small via ONNX @ 5 Hz
  → /depth_image     (sensor_msgs/Image, 32FC1)
  → /depth_pointcloud (sensor_msgs/PointCloud2)
```

CPU optimizations:
- `torch.inference_mode()` — skip gradient tracking
- ONNX runtime — ~30% faster than PyTorch on CPU
- 5 Hz publish rate — avoidance doesn't need faster at low drone speeds

> **Scale:** depth is relative. Fuse with barometer altitude for metric scale.

### 5.2 GPU Upgrade Path (~2 months)

No architectural changes. Only swap inside the depth node:

1. `sudo apt install cuda-toolkit` (NVIDIA WSL2 driver package)
2. Change model: `Depth Anything V2 Large`
3. Remove downsampling → full 960×720
4. Raise rate: 5 Hz → 15–20 Hz
5. `model.to("cuda")` instead of ONNX

Expected: ~5 fps CPU → ~25 fps GPU.

---

## Phase 6 — Collision Avoidance  `GPU recommended` `LATER`

> Requires Phase 5 depth output. Best done after GPU is available for reliable fps.

### 6.1 Avoidance Node — Velocity Gate

Sits **between** mission planner and drone driver on `/cmd_vel`:

```
/cmd_vel_planned  (from mission_planner)
/depth_image      (from depth_estimator)
        ↓
  [avoidance_node]
        ↓
/cmd_vel          (to drone_driver)

logic:
  ROI = center 1/3 of depth image
  min_d = min(ROI)

  if min_d < STOP_DIST:   → zero velocity, hover
  if min_d < SLOW_DIST:   → scale down forward velocity
  else:                   → pass through planned velocity unchanged
```

- [ ] Create `tello_avoidance` package
- [ ] STOP_DIST default: 0.8 m
- [ ] SLOW_DIST default: 1.5 m
- [ ] Both params in a YAML config file (not hardcoded)

### 6.2 RViz Perception Display

- [ ] `DepthCloud` display → `/depth_pointcloud`
- [ ] Marker display for stop/slow zones

---

## Phase 7 — Full Integration  `FINAL`

### 7.1 Full Launch File

`launch/tello_full.launch.py`:
```
drone_driver          (tello_ros)
camera_info_publisher (config/calibration.yaml)
rtabmap               (visual odometry)
depth_estimator       (Depth Anything V2)
avoidance_node
mission_planner
mosaic_capture
rviz2                 (config/tello.rviz)
```

### 7.2 RViz Final Layout

| Panel | Topic | Type |
|-------|-------|------|
| Camera | `/image_raw` | Image |
| Depth cloud | `/depth_pointcloud` | PointCloud2 |
| Drone TF | `/tf` | TF |
| Odom path | `/rtabmap/odom` | Odometry |
| Map | `/rtabmap/map` | OccupancyGrid |
| Mission status | `/mission_status` | Marker |

### 7.3 Test Checklist

- [ ] Phases 1–4: indoor grid flight + mosaic, no avoidance
- [ ] Phase 5: depth visualized in RViz, scale roughly correct vs tape measure
- [ ] Phase 6: fly toward wall → auto-stop at STOP_DIST
- [ ] Phase 7: full mission with avoidance active, mosaic output

---

## Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| WSL2 can't reach Tello WiFi | `networkingMode=mirrored`; test `ping 192.168.10.1` before every session |
| Losing internet while connected to Tello | Use phone USB tether for internet; long-term: USB WiFi dongle |
| UDP packet loss | Keep drone within 5 m; reduce video bitrate in driver params |
| RViz lag via WSLg | Use `image_transport` compressed; lower driver resolution |
| rtabmap CPU overload | `Vis/MaxFeatures=500`, `Kp/MaxFeatures=500` |
| Depth inference too slow on CPU | ONNX + 320×240 + 5 Hz; cap drone speed to 0.5 m/s |
| Monocular depth scale drift | Fuse with barometer; recalibrate scale factor each flight |
| Tello battery (~13 min) | Short grid segments; land at 20% battery |
| Mosaic blur from motion | Capture only when `cmd_vel ≈ 0` (settled pose) |

---

## Package Summary

| Package | Layer | Purpose |
|---------|-------|---------|
| `tello_ros` | 1 — Drone interface | Tello SDK ↔ ROS 2 bridge |
| `ros2_shared` | 1 | tello_ros dependency |
| `camera_calibration` | 2 | Camera intrinsics |
| `rtabmap_ros` | 2 | Visual odometry + SLAM |
| `mosaic_capture` *(custom)* | 3 | Image + pose logger |
| `stitch_mosaic.py` *(script)* | 3 | Offline mosaic stitching |
| `mission_planner` *(custom)* | 4 | Grid waypoint planner |
| `depth_estimator` *(custom)* | 5 | Monocular depth (swappable model) |
| `tello_avoidance` *(custom)* | 6 | Velocity-gate collision avoidance |

---

## Repository Structure (Target)

```
tello-drone/
├── PLAN.md
├── tello_ws/
│   └── src/
│       ├── tello_ros/            (cloned — Layer 1)
│       ├── ros2_shared/          (cloned — dependency)
│       ├── depth_estimator/      (custom — Layer 5, model-agnostic)
│       ├── tello_avoidance/      (custom — Layer 6)
│       ├── mission_planner/      (custom — Layer 4)
│       └── mosaic_capture/       (custom — Layer 3)
├── config/
│   ├── tello.rviz
│   ├── calibration.yaml
│   └── avoidance_params.yaml
├── data/
│   └── images/
├── scripts/
│   └── stitch_mosaic.py
└── launch/
    ├── tello_base.launch.py      (Phases 1–2 only)
    ├── tello_mission.launch.py   (Phases 1–4)
    └── tello_full.launch.py      (All phases)
```

---

## Execution Order

| Step | Phase | Requires GPU? |
|------|-------|---------------|
| 1. WSL2 + ROS 2 install | 0 | no |
| 2. RViz display test | 0 | no |
| 3. First takeoff/land via ROS | 1 | no |
| 4. Camera calibration | 2 | no |
| 5. Visual odometry in RViz | 2 | no |
| 6. Image capture node | 3 | no |
| 7. Mosaic stitching script | 3 | no |
| 8. Grid mission flight | 4 | no |
| 9. Depth estimator node | 5 | no (slow) |
| 10. Depth visualization in RViz | 5 | no (slow) |
| 11. *(get GPU)* | — | — |
| 12. Upgrade depth model + full res | 5 | yes |
| 13. Avoidance node + tuning | 6 | yes |
| 14. End-to-end full mission | 7 | yes |
