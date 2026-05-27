# Tello Drone Project — Development Diary

---

## Session 1 — 2026-05-18

### Goal
Start Phase 0: get WSL2 + ROS 2 Humble running on Windows 11 as the foundation for the Tello ROS stack.

### Context
- Hardware: DJI Tello drone (standard model, not EDU)
- Machine: Windows 11 notebook, WiFi only (no ethernet, no USB dongle)
- Connectivity plan: notebook WiFi switches to Tello AP when flying; phone USB tether for internet in the meantime
- CPU only for now; GPU expected ~2026-07

### Plan reference
Full project plan in `PLAN.md`. Phases ordered CPU-first:
- Phases 0–4: no GPU needed (setup, driver, odometry, mosaic, mission)
- Phases 5–6: depth estimation + collision avoidance (deferred until GPU)

---

### Steps Attempted

#### Step 1 — WSL2 config (`.wslconfig`)
- Opened `%USERPROFILE%\.wslconfig` via notepad
- Added `networkingMode=mirrored` under `[wsl2]`
- Ran `wsl --shutdown`

**Result:** ❌ Error — WSL2 is not installed at all.
```
The Windows Subsystem for Linux is not installed.
```

#### Step 2 — WSL2 Install
- Ran `wsl --install` as Administrator → downloaded WSL2 kernel 2.7.3
- Required **two reboots** to fully enable VirtualMachinePlatform feature
- After second reboot: `wsl --install -d Ubuntu-22.04` succeeded
- Ubuntu 22.04 launched, created user `riris`

**Result:** ✅ Ubuntu 22.04 running inside WSL2

#### Step 3 — Phase 0 Verification
- `.wslconfig` confirmed: `networkingMode=mirrored` ✅
- `echo $DISPLAY` returned `:0` → WSLg active ✅
- `sudo apt update && sudo apt upgrade -y` → clean, no errors ✅

#### Step 4 — ROS 2 Humble Install
- Added ROS 2 apt repo and GPG key ✅
- Installed `ros-humble-desktop`, `python3-colcon-common-extensions`, `python3-rosdep` ✅
- Ran `sudo rosdep init && rosdep update` ✅
- Added `source /opt/ros/humble/setup.bash` to `~/.bashrc` ✅

#### Step 5 — RViz2 Test
- `ros2 doctor` ran but hung on network discovery (DDS timeout) — cancelled with Ctrl+C, not an error
- `ros2 run rviz2 rviz2` launched successfully
- RViz window appeared on Windows desktop via WSLg ✅
- OpenGL 4.1, running at **31 fps** ✅
- Expected warnings: `No tf data` (no drone connected yet), `Global Status: Warn` (normal when idle)

**Result:** ✅ Phase 0 fully complete

---

### Notes & Observations
- `ros2 --version` flag doesn't exist in ROS 2 — use `ros2 run` or `ros2 topic list` to verify install
- `ros2 doctor` hangs for ~60s on network probe — skip it, not useful day-to-day
- WSL2 needed **2 reboots** during install (VirtualMachinePlatform enablement)
- Copy/paste in default Windows console: right-click to paste. Recommend installing **Windows Terminal** from MS Store for better experience

---

### Blockers
None — Phase 0 complete.

### Next — Phase 1
1. Create ROS 2 workspace (`~/tello_ws`)
2. Clone `tello_ros` and `ros2_shared`
3. Build with `colcon`
4. Connect to Tello AP and do first takeoff/land from ROS

---

## Session 2 — 2026-05-18 (continued)

### Goal
Phase 1: Build tello_ros workspace, first drone connection and takeoff via ROS.

### Steps

#### Step 1 — Workspace & driver clone
```bash
mkdir -p ~/tello_ws/src
git clone tello_ros + ros2_shared
```
**Result:** ✅ Both cloned successfully

#### Step 2 — rosdep + colcon build (attempt 1)
**Result:** ❌ `tello_description` failed — `replace.py` missing execute permission
**Fix:** `chmod +x .../replace.py`

#### Step 3 — colcon build (attempt 2)
**Result:** ❌ `tello_driver` failed — `asio.hpp` not found + `rclcpp_components/register_node_macro.hpp` not found
**Fix:** `sudo apt install -y libasio-dev` (rclcpp_components was already installed)

#### Step 4 — colcon build (attempt 3)
**Result:** ❌ `register_node_macro.hpp` still not found
**Diagnosis:** Header exists at `/opt/ros/humble/include/rclcpp_components/rclcpp_components/register_node_macro.hpp` but `rclcpp_components` was missing from `DRIVER_NODE_DEPS` and `JOY_NODE_DEPS` in `tello_driver/CMakeLists.txt` — so its include path was never passed to the compiler
**Fix:** Added `rclcpp_components` to both dep lists in CMakeLists.txt

#### Step 5 — colcon build (attempt 4)
**Result:** ✅ All 5 packages finished clean
```
Summary: 5 packages finished [26.5s]
tello_msgs / ros2_shared / tello_description / tello_driver / tello_gazebo
```

#### Step 6 — Source workspace
```bash
source ~/tello_ws/install/setup.bash
echo "source ~/tello_ws/install/setup.bash" >> ~/.bashrc
```
**Result:** ✅ Workspace sourced and added to .bashrc permanently

### Notes
- `tello_ros` CMakeLists.txt has a bug: `rclcpp_components` is found via `find_package` but not added to target deps — causes build failure on ROS 2 Humble due to new include path layout
- Fix is permanent in the cloned source; will survive rebuilds

### Next
- Power on Tello, connect Windows WiFi to Tello AP (`TELLO-XXXXXX`)
- Verify `ping 192.168.10.1` from WSL
- Launch `tello_driver` and verify topics
- First takeoff/land via ROS service call

---

## Session 3 — 2026-05-18 (continued)

### Goal
Fix ROS 2 inter-process communication — two WSL2 terminals on the same machine cannot exchange topics.

### Symptom
- Terminal 1: `ros2 topic pub` publishes fine, no errors
- Terminal 2: `ros2 topic echo` hangs indefinitely, prints nothing
- This is a fundamental DDS communication failure, not a Tello issue

### What We Tried

| Attempt | Config | Result |
|---------|--------|--------|
| Default FastDDS | nothing | `ros2 topic list` hangs |
| FastDDS + `ROS_LOCALHOST_ONLY=1` | env var | still hangs |
| Switch to CycloneDDS | `RMW_IMPLEMENTATION=rmw_cyclonedds_cpp` | still hangs |
| CycloneDDS + `ROS_LOCALHOST_ONLY=1` | both | still hangs |
| CycloneDDS + XML config, interface `lo` | `~/.cyclonedds.xml` | still hangs |
| CycloneDDS + XML config, interface `loopback0` | `~/.cyclonedds.xml` | error: interface not found |
| CycloneDDS + unicast to 127.0.0.1, no multicast | `~/.cyclonedds.xml` | still hangs |

### Analysis — Root Cause Suspects

The fact that **both FastDDS and CycloneDDS fail**, and that **even unicast UDP to 127.0.0.1 fails**, rules out DDS configuration as the cause. Something is blocking UDP communication at a lower level.

**Suspect 1 — Windows Firewall (most likely)**
Windows Defender Firewall treats WSL2 network interfaces as "Public" networks and aggressively blocks UDP traffic between processes. DDS discovery uses UDP multicast (239.255.0.1) and random UDP ports, all of which can be silently dropped.

**Suspect 2 — VPN software**
Corporate/university VPN clients (Cisco AnyConnect, GlobalProtect, FortiClient, etc.) intercept the Windows network stack and can completely break WSL2 UDP traffic — even loopback. If a VPN is installed but not actively connected, the network driver may still be interfering.

**Suspect 3 — Antivirus / endpoint security**
Some AV products (Kaspersky, ESET, Sophos) intercept UDP packets and can silently drop DDS discovery traffic.

**Suspect 4 — WSL2 kernel sysctl**
`net.ipv4.conf.lo.accept_local` or similar settings could prevent loopback UDP from being delivered. Less likely but possible.

### Root Cause Identified
**Mullvad VPN** — installs a WireGuard kernel-level network driver that blocks UDP traffic on WSL2 interfaces even when not actively connected to a VPN server. User confirmed this matches a firewall issue they had to work around in the previous (lost) repo.

### Resolution Plan
Before every ROS session, run in PowerShell as Administrator:
```powershell
# Disable firewall (before working)
Set-NetFirewallProfile -Profile Domain,Public,Private -Enabled False

# Re-enable firewall (after working)
Set-NetFirewallProfile -Profile Domain,Public,Private -Enabled True
```
Long-term: add a proper WSL2 firewall exception rule so this isn't needed every session.

---

## End of Day Summary — 2026-05-18

### What Was Accomplished Today
- ✅ WSL2 installed (required 2 reboots)
- ✅ Ubuntu 22.04 running
- ✅ ROS 2 Humble installed and verified
- ✅ RViz2 opens and runs at 31 fps via WSLg
- ✅ `tello_ros` + `ros2_shared` cloned and built (fixed 3 build errors)
- ✅ Tello drone reachable from WSL (`ping 192.168.10.1` — 0% packet loss)
- ✅ `tello_driver_main` launches and connects to drone
- ❌ Root cause of DDS misidentified as Mullvad VPN (was actually stale ros2cli daemon)

### Build Fixes Applied to tello_ros
1. `chmod +x tello_description/src/replace.py` — missing execute permission
2. `sudo apt install libasio-dev` — missing ASIO networking library
3. Added `rclcpp_components` to `DRIVER_NODE_DEPS` and `JOY_NODE_DEPS` in `tello_driver/CMakeLists.txt` — header include path not passed to compiler in ROS 2 Humble

### Current ~/.bashrc additions
```bash
source /opt/ros/humble/setup.bash
source ~/tello_ws/install/setup.bash
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
export CYCLONEDDS_URI=file://$HOME/.cyclonedds.xml
```

### ~/.cyclonedds.xml current state
```xml
<CycloneDDS>
  <Domain>
    <General>
      <AllowMulticast>false</AllowMulticast>
    </General>
    <Discovery>
      <Peers>
        <Peer address="127.0.0.1"/>
      </Peers>
      <ParticipantIndex>auto</ParticipantIndex>
    </Discovery>
  </Domain>
</CycloneDDS>
```

---

---

## Session 4 — 2026-05-19

### Goal
Fix DDS inter-terminal communication, then first drone flight via ROS.

### Root Cause Found — Stale ros2cli Daemon

All previous DDS hanging was caused by a **stale ros2cli daemon** left over from a previous session. Every `ros2 topic list` / `ros2 topic echo` command first connects to this daemon via TCP XML-RPC. The daemon was in a bad state, causing a `TimeoutError: [Errno 110] Connection timed out` that made every command hang silently.

This was **not** a DDS configuration problem. UDP on loopback (127.0.0.1) was confirmed working throughout via raw `nc` test.

**Proof:** `echo "hello" | nc -u -w2 127.0.0.1 9999` → received instantly in Terminal 2.

### Fix — Start of Every Session
```bash
ros2 daemon stop
sleep 2
ros2 daemon start
```
Run this once in any WSL terminal before starting ROS work. If commands start hanging again, this is the first thing to try.

### What Was Accomplished
- ✅ Root cause of all DDS hanging identified: stale ros2cli daemon (not VPN, not firewall, not DDS config)
- ✅ `ros2 topic pub` + `ros2 topic echo` confirmed working between two terminals
- ✅ `ros2 topic list` returns instantly after daemon restart
- ✅ `tello_driver` connects to drone, `/flight_data` flows at 10 Hz, `/image_raw` video streaming
- ✅ Takeoff/land service calls confirmed reaching drone (`rc=1` = OK in this driver means command sent)
- ✅ Correct topic names confirmed: `/flight_data`, `/image_raw`, `/cmd_vel`, `/tello_response` (no `/tello/` prefix)
- ❌ Drone refused takeoff — `bat: 4` (4% battery, hard safety lock below ~10%)

### Key Learnings
- `rc=1` in TelloAction response = `OK` (command sent). `rc=2` = not connected. `rc=3` = busy.
- Topic names have NO `/tello/` prefix: use `/flight_data` not `/tello/flight_data`
- Always check `bat:` in `/flight_data` before attempting flight
- The "Unexpected 'ok'" warnings are benign — drone is responding, timing is fine

### Remaining CycloneDDS XML note
The `~/.cyclonedds.xml` still uses the deprecated `NetworkInterfaceAddress` element (CycloneDDS warns on startup). It works but should eventually be updated to the new syntax:
```xml
<CycloneDDS>
  <Domain>
    <General>
      <Interfaces>
        <NetworkInterface name="lo"/>
      </Interfaces>
      <AllowMulticast>false</AllowMulticast>
    </General>
    <Discovery>
      <Peers>
        <Peer address="127.0.0.1"/>
      </Peers>
      <ParticipantIndex>auto</ParticipantIndex>
      <MaxAutoParticipantIndex>9</MaxAutoParticipantIndex>
    </Discovery>
  </Domain>
</CycloneDDS>
```

---

## Session 4 — 2026-05-19 (continued) — First Flight ✅

### Goal
Achieve first ROS-controlled takeoff and land with charged battery.

### Pre-flight fixes applied this session

#### CycloneDDS interface fix — switch from `eth0` to `lo`
When connected to the Tello AP, `eth0` changes IP from the home network address (192.168.50.194) to a Tello-assigned address (192.168.10.x). Multicast on that network fails:
```
ddsi_udp_conn_write to udp/239.255.0.1:7400 failed with retcode -1
```
**Fix:** Pin CycloneDDS to the loopback interface (`lo`) so DDS works regardless of which WiFi network is active.

`~/.cyclonedds.xml` final working config:
```xml
<CycloneDDS>
  <Domain>
    <General>
      <Interfaces>
        <NetworkInterface name="lo"/>
      </Interfaces>
      <AllowMulticast>false</AllowMulticast>
    </General>
    <Discovery>
      <Peers>
        <Peer address="127.0.0.1"/>
      </Peers>
      <ParticipantIndex>auto</ParticipantIndex>
      <MaxAutoParticipantIndex>9</MaxAutoParticipantIndex>
    </Discovery>
  </Domain>
</CycloneDDS>
```

#### Daemon kill workaround — required every session
`ros2 daemon stop` can itself hang if the daemon is in a bad state. Use `pkill` instead:
```bash
pkill -f "ros2 daemon" ; sleep 1 ; ros2 daemon start
```
Run this **once** at the start of every WSL session before any ROS work. This is the single most important step — skipping it causes all `ros2` CLI commands to hang silently.

### First Flight

**Battery:** `bat: 59` ✅  
**DDS:** CycloneDDS on `lo` interface — no multicast failures ✅  
**Daemon:** fresh after `pkill` ✅

```bash
# Takeoff
ros2 service call /tello_action tello_msgs/TelloAction "{cmd: 'takeoff'}"
# → rc: 1  (OK — command sent to drone)
# Drone lifted off ✅

# Land
ros2 service call /tello_action tello_msgs/TelloAction "{cmd: 'land'}"
# → rc: 1  (OK)
# Drone landed ✅
```

**Camera:** Video window appeared on Windows desktop via WSLg during flight ✅

**Result:** ✅ **Phase 1 complete — first ROS-controlled takeoff and land confirmed**

---

## End of Day Summary — 2026-05-19

### What Was Accomplished
- ✅ Root cause of all DDS hanging confirmed: stale ros2cli daemon (not VPN, not firewall)
- ✅ CycloneDDS pinned to `lo` interface — DDS now works on both home WiFi and Tello AP
- ✅ `pkill -f "ros2 daemon" ; sleep 1 ; ros2 daemon start` established as session start ritual
- ✅ `ros2 topic pub` + `ros2 topic echo` confirmed working between two terminals
- ✅ `tello_driver` connects, `/flight_data` at 10 Hz, `/image_raw` video streaming
- ✅ **First takeoff and land via ROS service calls — bat: 59**
- ✅ Camera window opened during flight (video feed visible)

### Current ~/.bashrc additions
```bash
source /opt/ros/humble/setup.bash
source ~/tello_ws/install/setup.bash
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
export CYCLONEDDS_URI=file://$HOME/.cyclonedds.xml
```

### Start-of-session checklist (every session)
1. `pkill -f "ros2 daemon" ; sleep 1 ; ros2 daemon start` — kill stale daemon
2. Switch WiFi to Tello AP when ready to fly
3. Terminal 1: `ros2 run tello_driver tello_driver_main` — wait for `Receiving state` + `Receiving video`
4. Terminal 2: `ros2 topic echo /flight_data` — verify `bat:` > 20 before flight
5. Takeoff: `ros2 service call /tello_action tello_msgs/TelloAction "{cmd: 'takeoff'}"`
6. Land: `ros2 service call /tello_action tello_msgs/TelloAction "{cmd: 'land'}"`

### Next — Phase 2
1. **Set up GitHub repo** — push PLAN.md, DIARY.md, patches to tello_ros; `.gitignore` build/install/log
2. **Camera calibration** — fix "Cannot get camera info" error; run `camera_calibration` package with checkerboard
3. **RViz2 setup** — subscribe to `/image_raw` and `/flight_data` for a live dashboard
4. **First `cmd_vel` flight** — publish to `/cmd_vel` for velocity control, not just takeoff/land

---

## Session 5 — 2026-05-26

### Goal
Run the test checklist (Tests 1–9) from PLAN.md. Short-term project goal clarified: **manual flight → capture photos → stitch mosaic → defect/anomaly detection**. Autonomous mission deferred.

### What Was Accomplished

#### Code written (all pushed to GitHub)
- ✅ GitHub repo created: https://github.com/RianRBPS/tello-drone
- ✅ Workspace moved from `~/tello_ws` to `~/tello-drone/tello_ws` (repo root)
- ✅ `.bashrc` updated: `source ~/tello-drone/tello_ws/install/setup.bash`
- ✅ Phase 2 branch: `camera_info_publisher` node, `tello_calibration.yaml` placeholder, `tello_base.launch.py`
- ✅ Phase 3 branch: `mosaic_capture` node, `stitch_mosaic.py`, smoke tests (all passing)
- ✅ Phase 4 branch: `mission_planner` node (grid + PD controller + state machine), 22/22 unit tests
- ✅ PLAN.md fully rewritten: new priority order, test checklist Tests 1–9, Phase 4 = defect detection (new), Phase 5 = autonomous (deferred)

#### Tests run
- ✅ **TEST 1 PASSED** — all packages visible: `camera_info_publisher`, `mission_planner`, `mosaic_capture`, `tello_*`
- ✅ **TEST 2 PASSED** — `camera_info_publisher` reads YAML, publishes `/camera_info` at correct rate with correct values (`width: 960`, `height: 720`, `k: [921.0...]`)
- ⚠️ **TEST 3 INCOMPLETE** — driver connected (`bat: 88`, `Receiving state`, `Receiving video`) but `/image_raw` never produced output due to H264 startup issue + WiFi dropping

### Issues Encountered

#### H264 decode errors at startup (normal — do not panic)
```
[h264] non-existing PPS 0 referenced
[h264] decode_slice_header error
[h264] no frame!
[ERROR] error decoding frame
```
These are **normal** for the first 3–10 seconds. The H264 decoder needs one IDR/keyframe before it can output frames. They clear by themselves once the Tello sends a keyframe. `/image_raw` will not publish until after the first successful decode.

#### WiFi auto-switching (root cause of Test 3 failure)
Windows detects the Tello AP has no internet and automatically switches back to the home network after ~20 seconds, causing:
```
[ERROR] No state received for 5s
[ERROR] No video received for 5s
[ERROR] Command timed out
```

**Attempted fix (DO NOT USE):**
```powershell
netsh wlan set autoconfig enabled=no interface="Wi-Fi"
```
This disables ALL WiFi management — networks stop appearing. Required a Windows restart to recover.

**Correct fix — run before each drone session:**
```powershell
# Prevent home network from auto-connecting (run as Administrator)
netsh wlan set profileparameter name="YOUR_HOME_WIFI_NAME" ConnectionMode=manual

# Restore after session
netsh wlan set profileparameter name="YOUR_HOME_WIFI_NAME" ConnectionMode=auto
```
Replace `YOUR_HOME_WIFI_NAME` with your actual home WiFi SSID. This stops Windows from auto-jumping back but keeps WiFi working normally.

#### Daemon pkill not working
`pkill -f "ros2 daemon"` sometimes reports the daemon is "already running" after the kill. Use the stronger version:
```bash
pkill -9 -f "ros2 daemon" ; sleep 2 ; ros2 daemon start
```

### Current State (end of session)
- Windows was restarted to recover WiFi (broken by the netsh autoconfig command)
- Test 3 needs to be re-run next session with the WiFi fix applied first

### Start-of-Session Checklist (updated)
```bash
# 1. Kill stale daemon (use -9 to be safe)
pkill -9 -f "ros2 daemon" ; sleep 2 ; ros2 daemon start

# 2. Source workspace
source /opt/ros/humble/setup.bash
source ~/tello-drone/tello_ws/install/setup.bash

# 3. Prevent Windows from auto-switching WiFi (PowerShell as Admin)
#    netsh wlan set profileparameter name="YOUR_HOME_WIFI" ConnectionMode=manual

# 4. Switch Windows WiFi to TELLO-XXXXXX

# 5. Terminal 1: start driver
ros2 run tello_driver tello_driver_main
# Wait for: "Receiving state" AND "Receiving video"
# H264 errors after "Receiving video" are NORMAL — wait 10–15 seconds

# 6. Terminal 2: immediately after "Receiving video" appears:
ros2 topic hz /image_raw
# Should show ~30 Hz within 10–15 seconds

# 7. Check battery before any flight
ros2 topic echo /flight_data   # bat: must be > 20
```

### Next Session — Resume Test 3
1. Apply WiFi fix (PowerShell `ConnectionMode=manual`) before connecting to Tello
2. Re-run Test 3: confirm `/image_raw` publishes at ~30 Hz
3. Run Test 4: `camera_info_publisher` with real video
4. Run Test 9: `mission_planner` starts in IDLE (drone on, not flying)
5. Tests 5–8 require flying — do after Tests 3, 4, 9 pass

---

## Session 6 — 2026-05-27

### Goal
Fix `/image_raw` never publishing. Confirm TEST 3 with drone. Begin TEST 4.

### Root Cause Investigation — 5 H264 Decoder Bugs

This session was spent diagnosing and fixing a chain of bugs in `tello_driver`
that prevented `/image_raw` from ever publishing, even with the drone connected
and streaming. All 5 fixes are committed to the `phase-4-mission-planner` branch.

#### Bug 1 — VLA stack overflow (`video_socket.cpp`)
`unsigned char bgr24[size]` allocated ~2 MB on the thread stack at 960×720×3.
This is a C99 VLA — not standard C++ — and risks a silent stack overflow on the
video socket thread.
**Fix:** Changed to `std::vector<unsigned char> bgr24(size)` (heap allocation).

#### Bug 2 — Decode loop exits on first SPS/PPS failure (`video_socket.cpp`)
`try/catch` wrapped the entire `while` loop. `decode_frame()` throws
`H264DecodeFailure` when `got_picture == 0` — which is normal for SPS/PPS NAL
units (parameter sets, not display frames). This exception exited the whole loop,
silently dropping all remaining frames in the buffer.
**Fix:** Moved `try/catch` inside the loop. One bad packet is skipped; the rest
continue processing.

#### Bug 3 — Wrong flush on SPS/PPS failure (`video_socket.cpp`)
An earlier version of the fix called `decoder_.flush()` on every decode exception.
`avcodec_flush_buffers` clears reference frames but NOT parameter sets — so
flushing after an SPS/PPS packet (which stored its data in the codec context)
would leave the codec needing a new IDR but the SPS/PPS were still intact. However,
flushing at the wrong time could cause the next IDR to fail because of cleared
reference state. The correct fix: do NOT flush on SPS/PPS exceptions; only flush
on genuine buffer overflows (packet loss).
**Fix:** Added `H264Decoder::flush()` method; call only on buffer overflow.

#### Bug 4 — `streamon` never sent when Tello was already streaming (root cause)
The timer callback sent `streamon` only when `!video_socket_->receiving()`. But
when the Tello was already streaming from a previous WiFi session, both state and
video sockets became active within ~175 ms of startup — before the 1-second timer
ever fired. So `!video_socket_->receiving()` was never true when the timer checked,
meaning `streamon` was never sent. We joined the H264 stream mid-GOP, missing the
SPS+PPS+IDR the decoder needs.
**Fix:** Added `streamon_sent_` flag. After state is established, always send
`streamon` once regardless of whether video is already flowing. This resets the
Tello encoder to a clean SPS+PPS+IDR.

#### Bug 5 — `consumed <= 0` break before `is_frame_available()` check
`av_parser_parse2` can return `consumed == 0` when it flushes a buffered NAL unit
(SPS or PPS) without consuming new input bytes. The safety break `if (consumed <= 0) break`
was placed before the `is_frame_available()` check, silently discarding the
just-flushed SPS/PPS packet before it could be decoded.
**Fix:** Moved the `consumed <= 0` break to AFTER the `is_frame_available()` block.
(Applied as a code improvement by user review.)

#### Additional fix — 15 fps publish cap
Publishing 960×720 BGR8 at 30 fps generates ~60 MB/s on the loopback socket plus
full H264 decode CPU on every frame. On a Surface laptop running WSL2 with
integrated graphics, this caused the entire machine to freeze (happened twice
this session requiring forced restarts).
**Fix:** Added `kMaxPublishHz = 15.0` constant and `last_frame_published_`
timestamp to `VideoSocket`. Frames beyond the rate cap are discarded before
serialisation. Set `kMaxPublishHz = 0` to disable. 15 fps is more than enough
for mosaic capture and visual odometry.

### TEST 3 — ✅ PASSED 2026-05-27

Key log lines confirming success:
```
[INFO] Sending streamon to reset video GOP
[INFO] Receiving video
[INFO] First frame decoded: 960x720 — /image_raw is live
```

Confirmed with `ros2 topic echo /image_raw --once` — returned a full 960×720
BGR8 frame with valid pixel data.

**Note:** "non-existing PPS 0 referenced" errors at startup are NORMAL. They
appear for 1–3 seconds while the first keyframe arrives after `streamon`. The
"First frame decoded" message confirms the pipeline is healthy.

### PC Freeze — Cause and Workaround
- Machine froze twice during this session while `tello_driver_main` ran
- Cause: 30 fps × 2 MB/frame = 60 MB/s loopback + concealment CPU on every frame
- Fix: 15 fps cap added (see Bug fix above)
- If machine still feels slow: watch Task Manager → WSL `vmmem` process; if it
  exceeds ~4 GB RAM or 80% CPU, stop the driver and let it settle

### Current State (end of session)
- ✅ TEST 3 PASSED — `/image_raw` publishing at ~15 Hz, confirmed with `echo --once`
- 🔲 TEST 4 ready — `camera_info_publisher` + `ros2 topic hz /camera_info`
- Machine was restarted at end of session (froze while driver was running)

### Start-of-Session Checklist (updated)
```bash
# 1. Fully quit Mullvad VPN (right-click tray → Quit, not just disconnect)
# 2. Switch Windows WiFi to TELLO-XXXXXX
# 3. In WSL:
pkill -9 -f "ros2 daemon" ; sleep 2 ; ros2 daemon start
source /opt/ros/humble/setup.bash
source ~/tello-drone/tello_ws/install/setup.bash

# Terminal 1 — driver
ros2 run tello_driver tello_driver_main
# Wait for: "Sending streamon to reset video GOP"
#           "Receiving video"
#           "First frame decoded: 960x720"
# Startup takes ~3–5 seconds after "Receiving video"

# Terminal 2 — confirm video (wait 5 s before reading rate)
ros2 topic hz /image_raw   # expect ~15 Hz
```

### Commits This Session
- `Fix /image_raw never publishing: VLA stack overflow + decode loop exit on error`
- `Fix /image_raw: always send streamon after connecting, even if already streaming`
- `Cap video publish rate at 15 fps to prevent WSL2 CPU overload`

### Next Session
1. ✅ TEST 3 — done
2. 🔲 TEST 4 — run `camera_info_publisher` alongside `tello_driver`, confirm `/camera_info` at ~15 Hz
3. 🔲 TEST 9 — `mission_planner` starts in IDLE (can do with drone just powered on, no flight)
4. 🔲 TEST 5 — camera calibration (need printed 8×6 checkerboard, 25 mm squares)

---

## Orientador — Feedback 2026-05-27

### Contexto
Reunião com orientador durante a sessão 6. Feedback sobre arquitetura e direção do projeto.

### Pontos levantados

#### 1 — Usar tentone/tello-ros2 como base
O orientador indicou o repositório **https://github.com/tentone/tello-ros2** como base
do driver em vez do `clydemcqueen/tello_ros` atual.

O tentone já publica todos os tópicos necessários sem nós extras:
- `/image_raw` — câmera 30 Hz
- `/camera_info` — calibração 2 Hz (já incluso — sem precisar do `camera_info_publisher`)
- `/imu` — aceleração e giroscópio 10 Hz
- `/odom` — posição estimada 10 Hz (sem precisar do rtabmap)
- `/tf` — transforms 10 Hz
- `/battery_state` — bateria 2 Hz

**Ação:** testar se o tentone builda no ROS 2 Humble (foi feito para Foxy).

#### 2 — Projeto tem código demais
O orientador observou que o repositório atual tem muito código (driver C++ com patches,
`camera_info_publisher`, `mission_planner`, scripts de teste) o que complica o projeto.

A visão correta é:
- Driver externo (tentone ou clydemcqueen) — clonado, não modificado, não entra no repo
- **Um único nó Python** escrito pelo aluno que faz subscribe dos tópicos e implementa
  a contribuição do projeto: captura → mosaico → detecção de defeitos

#### 3 — ros2 bag para desenvolvimento offline
O orientador recomendou usar **ros2 bag** para gravar uma sessão com o drone e depois
desenvolver e testar todos os nós sem precisar ligar o drone novamente.

Fluxo:
```
1. Ligar drone uma vez → ros2 bag record → gravar /image_raw /odom /imu /camera_info
2. Desligar drone
3. ros2 bag play → reproduz o experimento completo
4. Desenvolver e iterar sobre o nó customizado com o bag
```

**Benefício:** uma sessão de voo libera semanas de desenvolvimento offline.

#### 4 — Acesso aos dados via subscribe
Confirmação de que o aluno não precisa se preocupar com UDP, H264, sockets — o driver
já faz tudo isso. O trabalho do projeto é criar um nó que faz subscribe de `/image_raw`
e `/odom` e implementa a lógica de inspeção.

#### 5 — Visualização no RViz
Recomendação de configurar o RViz para visualizar câmera, IMU e odometria do tentone
antes de começar a escrever o nó customizado — confirma que todos os dados chegam
corretamente.

### Ações definidas
- 🔲 Testar `tentone/tello-ros2` no ROS 2 Humble
- 🔲 Ligar drone + gravar bag (TEST 10) — prioridade alta
- 🔲 Avaliar se `camera_info_publisher` e `mission_planner` podem ser removidos do repo
- 🔲 Simplificar estrutura do repo para: driver (externo) + 1 nó customizado
