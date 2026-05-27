# Tello Drone — Indoor Inspection & Mosaic Mapping
**Platform:** DJI Tello | ROS 2 Humble | WSL2 on Windows 11

---

## Short-Term Goal

Fly the drone **manually** inside a building, capture overlapping photos,
stitch them into a mosaic, and run **defect / anomaly detection** on the result
to find cracks, damage, or irregular surfaces.

Autonomous flight comes **after** this pipeline is proven end-to-end.

---

## Project Goals (ordered by priority)

| # | Goal | CPU-only? | Status |
|---|------|-----------|--------|
| 1 | Fly and control Tello via ROS 2 | yes | ✅ Done |
| 2 | Camera calibration + live video pipeline | yes | 🔲 Code ready, needs drone |
| 3 | Capture photos + stitch mosaic | yes | 🔲 Code ready, needs drone |
| 4 | Defect / anomaly detection on mosaic | yes | 🔲 Not yet written |
| 5 | Basic autonomous grid mission | yes | 🔲 Code ready, deferred |
| 6 | Depth estimation (monocular) | partial | ⏳ Deferred — wait for GPU |
| 7 | Collision avoidance | needs GPU | ⏳ Deferred — wait for GPU |

---

## Current Status (2026-05-27)

### Orientação do professor (2026-05-27)
- Usar **https://github.com/tentone/tello-ros2** como base do driver — já publica
  `/image_raw`, `/camera_info`, `/imu`, `/odom`, `/tf` sem precisar de nós extras
- Gravar **ros2 bag** na primeira sessão com drone → desenvolver tudo offline depois
- O projeto deve ser **um único nó customizado** que faz subscribe dos tópicos do driver
  e implementa: captura → mosaico → detecção de defeitos
- 🔲 Migrar de `clydemcqueen/tello_ros` para `tentone/tello-ros2` (verificar compatibilidade Humble)

### What is confirmed working
- ✅ WSL2 + ROS 2 Humble installed and verified
- ✅ `tello_ros` built (3 build errors fixed and committed)
- ✅ `tello_driver` connects to drone, `/flight_data` at 10 Hz, `/image_raw` video
- ✅ Takeoff and land via ROS service call (bat: 59, first flight 2026-05-19)
- ✅ GitHub repo: https://github.com/RianRBPS/tello-drone
- ✅ `/image_raw` video pipeline working at ~15 Hz (5 H264 decoder bugs fixed 2026-05-27)

### Code written but NOT yet tested with drone
- 🔲 `camera_info_publisher` node (Phase 2)
- 🔲 `tello_base.launch.py` (Phase 2)
- 🔲 `mosaic_capture` node (Phase 3)
- 🔲 `stitch_mosaic.py` stitching script (Phase 3)
- 🔲 `mission_planner` node (Phase 5 — deferred)

### Code tested without drone (offline tests pass)
- ✅ `stitch_mosaic.py` — both feature and pose methods (synthetic images)
- ✅ `mosaic_capture` node — fake ROS messages, 5 frames + poses.csv saved
- ✅ `mission_planner` — grid, PD controller, state machine logic (22/22 tests)

---

## Test Checklist — Run These in Order

Each step proves the next one is worth doing.
Run the start-of-session ritual first (every time):
```bash
# 0. Fully quit Mullvad VPN (right-click tray → Quit) — even "disconnected" blocks UDP
# 1. Switch Windows WiFi to TELLO-XXXXXX
# 2. In WSL:
pkill -9 -f "ros2 daemon" ; sleep 2 ; ros2 daemon start
source /opt/ros/humble/setup.bash
source ~/tello-drone/tello_ws/install/setup.bash
```

---

### ✅ TEST 1 — Workspace builds ✅ PASSED 2026-05-26
Confirms all packages are installed and importable.
```bash
ros2 pkg list | grep -E "tello|mosaic|camera_info|mission"
```
**Pass:** All 4 custom packages appear in the list.

---

### ✅ TEST 2 — camera_info_publisher ✅ PASSED 2026-05-26
Confirms the node reads the calibration YAML and re-publishes on `/camera_info`.
```bash
# Terminal 1
ros2 run camera_info_publisher camera_info_publisher \
  --ros-args -p calibration_file:=$HOME/tello-drone/config/tello_calibration.yaml

# Terminal 2
ros2 topic pub /image_raw sensor_msgs/Image \
  "{header: {frame_id: 'camera'}}" --once
ros2 topic echo /camera_info --once
```
**Pass:** A `CameraInfo` message prints with `width: 960`, `height: 720`.

---

### ✅ TEST 3 — tello_driver connects (drone on + charged) ✅ PASSED 2026-05-27
Re-confirm the driver still works after workspace moved.
```bash
# Switch WiFi to Tello AP first
# Terminal 1
ros2 run tello_driver tello_driver_main

# Terminal 2
ros2 topic echo /flight_data   # confirm bat > 20
ros2 topic echo /image_raw     # confirm video frames arriving
```
**Pass:** `bat:` field shows battery %, image messages stream.

**Notes:** Required 5 code fixes to unlock video (all committed):
1. VLA `unsigned char bgr24[size]` → `std::vector<>` (stack overflow at 960×720×3)
2. Inner try/catch — outer catch was exiting the decode loop on first SPS/PPS failure
3. No flush on SPS/PPS decode failure — flush was wiping the parameter sets just stored
4. Always send `streamon` on connect — driver was joining stream mid-GOP when Tello was already streaming
5. `consumed <= 0` break moved AFTER `is_frame_available()` — buffered SPS/PPS flushed by parser (consumed==0) was being discarded before decode

---

### 🔲 TEST 4 — camera_info_publisher with real video (drone on)
Confirms timestamps sync correctly with the real camera feed.
```bash
# While tello_driver is running (Terminal 1 from Test 3):
# Terminal 2
ros2 run camera_info_publisher camera_info_publisher \
  --ros-args -p calibration_file:=$HOME/tello-drone/config/tello_calibration.yaml

# Terminal 3 — wait 5 seconds before reading the rate
ros2 topic hz /camera_info   # should be ~15 Hz (driver capped at 15 fps)
ros2 topic hz /image_raw     # should also be ~15 Hz
```
**Pass:** `/camera_info` and `/image_raw` both publish at ~15 Hz.
Note: driver publish rate capped at 15 fps (was 30) to prevent WSL2 CPU overload on integrated-GPU machines. Cap is `kMaxPublishHz` in `tello_driver_node.hpp`.

---

### 🔲 TEST 5 — Camera calibration (drone on, checkerboard needed)
Replaces the placeholder YAML with real intrinsics.
Print an 8×6 checkerboard (25 mm squares) and run:
```bash
ros2 run camera_calibration cameracalibrator \
  --size 8x6 --square 0.025 \
  image:=/image_raw camera:=/camera
```
Move the checkerboard in front of the camera until the
X / Y / Size / Skew bars all turn green. Click **Calibrate → Save**.
Copy the output YAML over `config/tello_calibration.yaml`.

**Pass:** Calibration saves successfully; `config/tello_calibration.yaml` updated.

---

### 🔲 TEST 6 — rtabmap visual odometry (drone on + flying)
```bash
# Terminal 3 (driver + camera_info already running)
ros2 launch rtabmap_launch rtabmap.launch.py \
  visual_odometry:=true \
  rgb_topic:=/image_raw \
  camera_info_topic:=/camera_info \
  frame_id:=base_link \
  Vis/MaxFeatures:=500 \
  Kp/MaxFeatures:=500

# Terminal 4
ros2 topic echo /rtabmap/odom
```
Takeoff, move drone by hand slowly. Watch the odom x/y/z values change.

**Pass:** `/rtabmap/odom` publishes and position changes when drone moves.

---

### 🔲 TEST 7 — mosaic_capture saves real frames (drone flying)
```bash
# Terminal 5
ros2 run mosaic_capture mosaic_capture

# Fly manually, then land and check:
ls ~/tello-drone/data/images/
cat ~/tello-drone/data/images/poses.csv
```
**Pass:** At least 3–4 `frame_XXXX.jpg` files saved, `poses.csv` has matching rows.

---

### 🔲 TEST 8 — stitch_mosaic.py on real frames
```bash
python3 ~/tello-drone/scripts/stitch_mosaic.py
# Output: ~/tello-drone/data/mosaic.png
```
**Pass:** `mosaic.png` is created and shows a wider view than any single frame.

---

### 🔲 TEST 9 — mission_planner node starts (drone on, NOT flying)
Confirms the node initialises and sits in IDLE without sending spurious commands.
```bash
ros2 run mission_planner mission_planner

# In another terminal:
ros2 topic echo /mission_status   # should print: IDLE — call /tello_action takeoff...
ros2 topic echo /cmd_vel          # should be SILENT (no velocity commands in IDLE)
```
**Pass:** Status = IDLE, no `/cmd_vel` messages published.

---

### 🔲 TEST 10 — Gravar ros2 bag (drone ligado, voando manualmente)
Grava todas as mensagens do experimento para reprodução offline.
Após este teste o drone **não precisa mais ser ligado** para desenvolver e testar nós.

```bash
# Com o driver rodando (Terminal 1), gravar em Terminal 2:
ros2 bag record /image_raw /flight_data /camera_info /tf -o ~/tello-drone/data/bags/voo_01

# Voar manualmente por 30–60 segundos cobrindo a área de inspeção
# Ctrl-C para parar a gravação

# Verificar o bag gerado:
ros2 bag info ~/tello-drone/data/bags/voo_01
```

**Pass:** Bag criado com pelo menos `/image_raw` e `/flight_data`. Tamanho esperado: ~50–100 MB por minuto de voo.

#### Reproduzir o bag (sem drone, sem WiFi)
```bash
# Terminal 1 — reproduz todas as mensagens gravadas
ros2 bag play ~/tello-drone/data/bags/voo_01

# Terminal 2 — seu nó processa como se o drone estivesse ao vivo
ros2 run mosaic_capture mosaic_capture

# Terminal 3 — visualizar
ros2 run rqt_image_view rqt_image_view
```

**Benefício:** Desenvolva e teste `mosaic_capture`, `defect_detector` e qualquer outro nó sem ligar o drone.

---

## Architecture Overview

```
Windows 11 (notebook WiFi → Tello AP)
└── WSL2 (Ubuntu 22.04) — networkingMode=mirrored
    └── ROS 2 Humble
        │
        ├── [LAYER 1 — DRONE INTERFACE]        ✅ working
        │   └── tello_driver          /image_raw, /flight_data, /cmd_vel
        │
        ├── [LAYER 2 — STATE ESTIMATION]       🔲 code ready
        │   ├── camera_info_publisher
        │   └── rtabmap (visual odometry)
        │
        ├── [LAYER 3 — CAPTURE]                🔲 code ready
        │   └── mosaic_capture node
        │
        ├── [LAYER 4 — ANALYSIS]               🔲 not yet written
        │   └── defect_detector (offline script)
        │
        └── [LAYER 5 — AUTONOMOUS]             🔲 code ready, deferred
            └── mission_planner node
```

---

## Phase 0 — Environment Setup ✅ DONE

- ✅ WSL2 installed (2 reboots required)
- ✅ Ubuntu 22.04 running
- ✅ ROS 2 Humble installed and verified
- ✅ RViz2 opens at 31 fps via WSLg
- ✅ `.wslconfig`: `networkingMode=mirrored`

---

## Phase 1 — Drone Interface & Basic Control ✅ DONE

- ✅ `tello_ros` + `ros2_shared` cloned and built
- ✅ 3 build errors fixed (committed to GitHub)
- ✅ First takeoff/land via ROS service call
- ✅ `/flight_data`, `/image_raw`, `/cmd_vel` confirmed

**Start-of-session checklist (every session):**
```bash
pkill -f "ros2 daemon" ; sleep 1 ; ros2 daemon start
# Switch WiFi to Tello AP
# Terminal 1: ros2 run tello_driver tello_driver_main
# Terminal 2: ros2 topic echo /flight_data  → confirm bat > 20
# Takeoff:    ros2 service call /tello_action tello_msgs/TelloAction "{cmd: 'takeoff'}"
# Land:       ros2 service call /tello_action tello_msgs/TelloAction "{cmd: 'land'}"
```

---

## Phase 2 — Camera Pipeline 🔲 CODE READY

Branch: `phase-2-state-estimation`

### 2.1 camera_info_publisher
Reads `config/tello_calibration.yaml`, publishes `/camera_info` in sync with `/image_raw`.
→ **Tests 2, 4**

### 2.2 Camera Calibration
Replaces placeholder YAML with real Tello intrinsics.
→ **Test 5** — needs printed checkerboard (8×6 at 25 mm square size).

### 2.3 rtabmap Visual Odometry (CPU-tuned)
→ **Test 6**

**CPU tuning:** `Vis/MaxFeatures=500`, `Kp/MaxFeatures=500`
**GPU upgrade path (~2026-07):** raise to 1000+, full 960×720

---

## Phase 3 — Mosaic Capture & Stitching 🔲 CODE READY

Branch: `phase-3-mosaic`

### 3.1 mosaic_capture node
- Saves `frame_XXXX.jpg` every 0.5 m of lateral travel (configurable)
- Logs pose to `data/images/poses.csv`
- Manual trigger: `ros2 topic pub /mosaic_capture/trigger std_msgs/Empty '{}'`
→ **Test 7**

### 3.2 stitch_mosaic.py (offline)
```bash
python3 scripts/stitch_mosaic.py               # feature-based (default)
python3 scripts/stitch_mosaic.py --method pose # pose-guided (uses poses.csv)
```
→ **Test 8**

---

## Phase 4 — Defect / Anomaly Detection 🔲 NOT YET WRITTEN

**Goal:** Analyze captured frames or the stitched mosaic to highlight cracks,
damage, or irregular surfaces. Runs **offline after landing** — no ROS needed.

**Input:** `data/images/frame_XXXX.jpg` or `data/mosaic.png`
**Output:** annotated images + JSON report in `data/analysis/`

### Planned approach (CPU-friendly)

| Method | When to use | CPU cost |
|--------|------------|---------|
| Classical — edge + contour | Structured surfaces (concrete, tile) | Very low |
| Texture anomaly (statistical) | Any repeating-pattern surface | Low–medium |
| Lightweight ONNX model | When classical methods miss subtle defects | Medium |

### Planned pipeline
```
frame_XXXX.jpg (or mosaic.png)
  → pre-process (resize, denoise, grayscale)
  → anomaly score per region (sliding window)
  → overlay heatmap on original image
  → save to data/analysis/frame_XXXX_annotated.jpg
  → write data/analysis/report.json
```

**Open questions before writing:**
- What surface type? (concrete, wood, metal, other)
- Do we have reference "normal" images, or detect without reference (unsupervised)?

---

## Phase 5 — Autonomous Mission 🔲 CODE READY (deferred)

Branch: `phase-4-mission-planner`
Config: `config/mission_params.yaml`

Deferred until Phase 3 is proven end-to-end. Code is written + tested (22/22).
Only needs PID gain tuning once real odometry data is available.

**State machine:** `IDLE → FLY → SETTLE → CAPTURE → NEXT → LAND → DONE`
**Safety:** emergency land at `battery_land_pct` (default 20%)
→ **Test 9**

---

## Phase 6 — Depth Estimation ⏳ DEFERRED (needs GPU ~2026-07)

Model: Depth Anything V2 Small via ONNX at 320×240 / 5 Hz on CPU.
GPU upgrade: switch to Large model, full 960×720, 15–20 Hz.

---

## Phase 7 — Collision Avoidance ⏳ DEFERRED (needs GPU)

Velocity-gate node between mission_planner and tello_driver.
Requires Phase 6 depth output.

---

## Modularity Principles

**Rule:** custom nodes talk only to ROS topics/services.
No node imports `tello_ros` directly. Swapping the drone = swap the driver, nothing else.

| Layer | Current | Future swap-in |
|-------|---------|---------------|
| **Drone hardware** | DJI Tello (WiFi UDP) | Any MAVLink drone, PX4 SITL |
| **Depth estimator** | Depth Anything V2 Small (CPU/ONNX) | Larger model on GPU |
| **Odometry** | rtabmap visual odometry | ORB-SLAM3, external VICON |
| **Mission planner** | Custom lightweight node | nav2, MAVSDK |
| **Map output** | OpenCV mosaic stitch | GeoTIFF, OccupancyGrid |

---

## Repository Structure

```
tello-drone/
├── PLAN.md                      ← this file
├── DIARY.md                     ← session log
├── config/
│   ├── tello_calibration.yaml   ← replace with real calibration output
│   └── mission_params.yaml      ← grid size, altitude, PID gains
├── data/
│   ├── images/                  ← captured frames + poses.csv (gitignored)
│   └── analysis/                ← annotated output (to be created, gitignored)
├── scripts/
│   ├── stitch_mosaic.py
│   ├── test_stitch.py
│   ├── test_mosaic_capture.py
│   └── test_mission_planner.py
├── launch/
│   └── tello_base.launch.py
└── tello_ws/src/
    ├── tello_ros/               ← cloned + patched
    ├── ros2_shared/             ← cloned
    ├── camera_info_publisher/   ← custom (Phase 2)
    ├── mosaic_capture/          ← custom (Phase 3)
    └── mission_planner/         ← custom (Phase 5, deferred)
```

---

## Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| WSL2 can't reach Tello WiFi | `networkingMode=mirrored`; test `ping 192.168.10.1` |
| ros2 CLI hangs | `pkill -f "ros2 daemon" ; sleep 1 ; ros2 daemon start` |
| rtabmap CPU overload | `Vis/MaxFeatures=500`, `Kp/MaxFeatures=500` |
| mosaic blur from motion | Capture only when drone is hovering still |
| Tello battery (~13 min) | Short sessions; land at 20% battery |
| Monocular depth scale drift | Fuse with barometer; deferred to GPU phase |
