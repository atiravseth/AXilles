# ESP32 DevkitV1 Dashboard - Quick Start Guide

---

## ⚠️ IMPORTANT: Upload Arduino Code First!
Before running the dashboard, you must upload the Arduino code to the ESP32:
1. Open Arduino IDE
2. Open `arduino/arduino_controller.ino`
3. Select "ESP32 Dev Module" as the board
4. Select the correct port (e.g., /dev/ttyUSB2)
5. Upload the code
6. Open Serial Monitor (115200 baud) - you should see "ESP32 Controller Starting..."

---

## 🚀 QUICK LAUNCH (Run These in Order)

**After opening terminal, run these commands one by one:**

```bash
# 0. Allow X11 access (needed for GUI)
xhost +local:docker

# 1. Start the container (if not running)
docker start ros2_humble

# 2. Check which port the ESP32 is on
ls -la /dev/ttyUSB* /dev/ttyACM*
# Example output: /dev/ttyUSB2 - use this in step 4

# 3. Fix permissions on the ESP32 port (replace with YOUR actual port)
sudo chmod 666 /dev/ttyUSB2

# 4. Start serial node with correct port (CHANGE ttyUSB2 if different!)
docker exec -d ros2_humble bash -c "source /opt/ros/humble/setup.bash && source /home/atiravs/ros2_humble_ws/install/setup.bash && ros2 run arduino_dashboard serial_node --ros-args -p port:=/dev/ttyUSB2"

# 5. Wait for connection
sleep 2

# 6. Verify data is flowing (should see pot/fsr/state values)
docker exec ros2_humble bash -c "source /opt/ros/humble/setup.bash && timeout 3 ros2 topic echo /arduino/pot --once"

# 7. Launch RQt GUI
docker exec ros2_humble bash -c "source /opt/ros/humble/setup.bash && source /home/atiravs/ros2_humble_ws/install/setup.bash && rqt --force-discover"
```

Then in RQt: **Plugins → Arduino Dashboard**

---

## Prerequisites
- ESP32 DevkitV1 connected (check port with `ls /dev/ttyUSB*` or `ls /dev/ttyACM*`)
- **ESP32 code uploaded** with the motor control firmware (arduino_controller.ino)
- TOF sensor (VL53L5CX) connected to I2C (SDA=D21, SCL=D22) - OPTIONAL
- Docker container `ros2_humble` exists

---

## 🔧 TROUBLESHOOTING

### Problem: ESP32 shows "try 0x..." errors
This means the ESP32 is crashing. Re-upload the Arduino code to the ESP32.

### Problem: No data on topics (simulation mode)
The ESP32 isn't connected or the port is wrong. Check:
1. Is ESP32 plugged in? (`ls /dev/ttyUSB*`)
2. Did you use the correct port in step 4?
3. Did you fix permissions? (`sudo chmod 666 /dev/ttyUSB2`)

### Problem: Container doesn't exist
```bash
# Check if container exists
docker ps -a | grep ros2_humble

# If not, create it:
docker run -d \
  --name ros2_humble \
  --net=host \
  -e DISPLAY=$DISPLAY \
  -v /tmp/.X11-unix:/tmp/.X11-unix:rw \
  -v ~/ros2_humble_ws:/home/atiravs/ros2_humble_ws \
  -v /dev:/dev \
  --privileged \
  osrf/ros:humble-desktop \
  sleep infinity
```

### Problem: ESP32 not detected
```bash
# Check ESP32 port on HOST (ESP32 usually shows as ttyUSB*, not ttyACM*)
ls -la /dev/ttyUSB* /dev/ttyACM* 2>/dev/null

# Check if visible in container
docker exec ros2_humble ls -la /dev/ttyUSB* /dev/ttyACM* 2>/dev/null

# Check which device is the ESP32
dmesg | grep -i "tty" | tail -10

# If port changed, may need to restart container
docker stop ros2_humble && docker start ros2_humble
```

### Problem: Permission denied on serial port
```bash
# For ESP32 (usually ttyUSB0)
docker exec ros2_humble chmod 666 /dev/ttyUSB0

# Or for Arduino (usually ttyACM0)
docker exec ros2_humble chmod 666 /dev/ttyACM0
```

### Problem: pyserial not installed
```bash
docker exec ros2_humble apt-get update
docker exec ros2_humble apt-get install -y python3-serial
```

### Problem: Serial node won't connect / Running in simulation mode
```bash
# Kill any existing serial nodes
docker exec ros2_humble pkill -f serial_node

# Check available serial ports
docker exec ros2_humble bash -c 'python3 -c "import serial.tools.list_ports; [print(f\"{p.device}: {p.description}\") for p in serial.tools.list_ports.comports()]"'

# Test serial directly with ESP32 (adjust port as needed)
docker exec ros2_humble bash -c 'python3 -c "import serial; s=serial.Serial(\"/dev/ttyUSB0\", 115200, timeout=1); print(s.readline()); s.close()"'

# Run serial node with explicit port
docker exec -d ros2_humble bash -c "source /opt/ros/humble/setup.bash && source /home/atiravs/ros2_humble_ws/install/setup.bash && ros2 run arduino_dashboard serial_node --ros-args -p port:=/dev/ttyUSB0"
```

### Problem: GUI not showing sensor data
```bash
# Check if serial node is running
docker exec ros2_humble bash -c "source /opt/ros/humble/setup.bash && ros2 node list"

# Enable GUI mode manually
docker exec ros2_humble bash -c "source /opt/ros/humble/setup.bash && ros2 topic pub /arduino/cmd/gui_on std_msgs/msg/Bool '{data: true}' --once"

# Check sensor data is flowing
docker exec ros2_humble bash -c "source /opt/ros/humble/setup.bash && timeout 3 ros2 topic echo /arduino/pot"
```

### Problem: Package not built / Code changes not taking effect
```bash
# Copy updated files from Sensors Lab to ROS2 workspace (if editing files in Sensors Lab folder)
cp "/home/atiravs/MRSD/GithubRepo_pawan/AXilles/MRSD Tasks/Sensors Lab/arduino_dashboard/arduino_dashboard/"*.py ~/ros2_humble_ws/SensorLab/arduino_dashboard/arduino_dashboard/

# Clean rebuild the package
docker exec ros2_humble bash -c "source /opt/ros/humble/setup.bash && cd /home/atiravs/ros2_humble_ws && rm -rf build/arduino_dashboard install/arduino_dashboard && colcon build --packages-select arduino_dashboard --symlink-install"
```

### Kill everything and restart
```bash
docker exec ros2_humble pkill -f serial_node
docker exec ros2_humble pkill -f rqt
sleep 1
docker exec -d ros2_humble bash -c "source /opt/ros/humble/setup.bash && source /home/atiravs/ros2_humble_ws/install/setup.bash && ros2 run arduino_dashboard serial_node"
sleep 2
docker exec ros2_humble bash -c "source /opt/ros/humble/setup.bash && source /home/atiravs/ros2_humble_ws/install/setup.bash && rqt --force-discover"
```

---

## 📋 FULL SETUP (First Time Only)

### Step 1: Create Docker Container

```bash
docker run -d \
  --name ros2_humble \
  --net=host \
  -e DISPLAY=$DISPLAY \
  -v /tmp/.X11-unix:/tmp/.X11-unix:rw \
  -v ~/ros2_humble_ws:/home/atiravs/ros2_humble_ws \
  -v /dev:/dev \
  --privileged \
  osrf/ros:humble-desktop \
  sleep infinity
```

### Step 2: Install Dependencies

```bash
docker exec ros2_humble apt-get update
docker exec ros2_humble apt-get install -y python3-serial
```

### Step 3: Build Package

```bash
docker exec ros2_humble bash -c "source /opt/ros/humble/setup.bash && cd /home/atiravs/ros2_humble_ws && colcon build --packages-select arduino_dashboard --symlink-install"
```

---

## 🧪 Quick Test Commands

### Check nodes:
```bash
docker exec ros2_humble bash -c "source /opt/ros/humble/setup.bash && ros2 node list"
```

### Check topics:
```bash
docker exec ros2_humble bash -c "source /opt/ros/humble/setup.bash && ros2 topic list | grep arduino"
```

### Test servo (move to 90°):
```bash
docker exec ros2_humble bash -c "source /opt/ros/humble/setup.bash && ros2 topic pub /arduino/cmd/servo std_msgs/msg/Int32 '{data: 90}' --once"
```

### Test DC motor (360 °/s = 1 rev/s):
```bash
docker exec ros2_humble bash -c "source /opt/ros/humble/setup.bash && ros2 topic pub /arduino/cmd/dc_vel std_msgs/msg/Int32 '{data: 360}' --once"
```

### Monitor velocity:
```bash
docker exec ros2_humble bash -c "source /opt/ros/humble/setup.bash && ros2 topic echo /arduino/velocity"
```

### Monitor TOF depth data:
```bash
docker exec ros2_humble bash -c "source /opt/ros/humble/setup.bash && ros2 topic echo /arduino/tof"
```

### Monitor potentiometer:
```bash
docker exec ros2_humble bash -c "source /opt/ros/humble/setup.bash && ros2 topic echo /arduino/potentiometer"
```

---

## ⏹️ Stop Everything

```bash
docker exec ros2_humble pkill -f serial_node
docker exec ros2_humble pkill -f rqt
```

---

## ROS2 Topics Reference

### Command Topics (GUI → ESP32)

| Topic | Type | Description |
|-------|------|-------------|
| `/arduino/cmd/servo` | Int32 | Servo angle (0-180°) |
| `/arduino/cmd/step` | Int32 | Stepper position |
| `/arduino/cmd/dc_vel` | Int32 | DC motor velocity (0-720 °/s, closed-loop) |
| `/arduino/cmd/dc_pos` | Int32 | DC motor target position (encoder counts) |
| `/arduino/cmd/dc_dir` | Bool | DC direction (false=CW, true=CCW) |
| `/arduino/cmd/dc_control_mode` | String | DC control mode ("velocity" or "position") |
| `/arduino/cmd/gui_on` | Bool | Enable/disable GUI mode |
| `/arduino/cmd/control_mode` | Int32 | Control mode (1=GUI, 2=Sensor) |

### Sensor Topics (ESP32 → GUI)

| Topic | Type | Description |
|-------|------|-------------|
| `/arduino/pot` | Int32 | Potentiometer value (0-4095, 12-bit ADC) |
| `/arduino/fsr` | Int32 | FSR sensor value (0-4095, moving average filtered) |
| `/arduino/encoder` | Int32 | Encoder position (counts) |
| `/arduino/velocity` | Int32 | Current motor velocity (°/s) |
| `/arduino/tof` | Int32MultiArray | TOF 8x8 depth data (64 values in mm) |
| `/arduino/state` | Int32 | Current control mode (1=GUI, 2=Sensor) |

---

## 🎮 TWO-STATE CONTROL SYSTEM

### State 1: GUI Control Mode (Default)
- **Servo**: Controlled by SERVO slider (0-180°)
- **DC Motor (Velocity Mode)**: Closed-loop velocity control (0-720 °/s) using encoder feedback
- **DC Motor (Position Mode)**: PID position control using encoder
- **Stepper**: Controlled by STEP slider
- **TOF Display**: 8x8 depth point cloud visualization (cyan dots on black background)

### State 2: Sensor-Based Autonomous Mode
- **Servo**: Controlled by potentiometer (maps to 10-170°)
- **DC Motor**: Controlled by potentiometer (maps to 0-255 PWM)
- **Stepper**: Controlled by FSR (pressure controls step speed and direction)
  - FSR > 2000: Steps CW (high pressure)
  - FSR 100-2000: Steps CCW (low pressure)
  - FSR moving average filter (window size 3) applied
- **Button (D4)**: Toggles DC motor direction with debounce (50ms)

### Switching Modes
Use the GUI buttons "🎮 GUI CONTROL" and "🤖 SENSOR AUTONOMOUS" to switch between modes.

Or via command line:
```bash
# Switch to GUI mode
docker exec ros2_humble bash -c "source /opt/ros/humble/setup.bash && ros2 topic pub /arduino/cmd/control_mode std_msgs/msg/Int32 '{data: 1}' --once"

# Switch to Sensor Autonomous mode
docker exec ros2_humble bash -c "source /opt/ros/humble/setup.bash && ros2 topic pub /arduino/cmd/control_mode std_msgs/msg/Int32 '{data: 2}' --once"
```

---

## ESP32 Command Protocol (GUI → ESP32)

| Command | Description | Example |
|---------|-------------|--------|
| `Vxxx` | Servo angle (0-180) | `V90` = 90 degrees |
| `Txxx` | Stepper position | `T100` = position 100 |
| `Wxxx` | DC motor velocity in °/s (closed-loop) | `W360` = 360 °/s (1 rev/s) |
| `Mxxx` | DC motor position target (position mode) | `M500` = position 500 |
| `Dx` | DC direction (0=CW, 1=CCW) | `D1` = counter-clockwise |
| `Qmode` | DC control mode | `Qvelocity` or `Qposition` |
| `Gx` | GUI enable (0=off, 1=on) | `G1` = enable |
| `Cx` | Control mode (1=GUI, 2=Sensor) | `C2` = sensor mode |
| `R0` | Reset all motors and state | `R0` = reset |

## ESP32 Response Protocol (ESP32 → GUI)

| Response | Description | Example |
|----------|-------------|--------|
| `Pxxx` | Potentiometer value (0-4095) | `P2048` = mid position |
| `Fxxx` | FSR sensor value (filtered, 0-4095) | `F500` = light pressure |
| `Exxx` | Encoder position | `E45` = position 45 |
| `Sxxx` | Current velocity (°/s) | `S180` = 180 °/s |
| `Xx` | Current control mode | `X1` = GUI mode, `X2` = Sensor mode |
| `Zd1,d2,...,d64` | TOF 8x8 depth data (mm) | `Z100,150,...` = 64 depth values |
