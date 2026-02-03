# Arduino Dashboard - Quick Start Guide

---

## 🚀 QUICK LAUNCH (Run These in Order)

**After opening terminal, run these commands one by one:**

```bash
# 1. Start the container (if not running)
docker start ros2_humble

# 2. Start serial node (background)
docker exec -d ros2_humble bash -c "source /opt/ros/humble/setup.bash && source /home/atiravs/ros2_humble_ws/install/setup.bash && ros2 run arduino_dashboard serial_node"

# 3. Wait 2 seconds for connection
sleep 2

# 4. Launch RQt GUI
docker exec ros2_humble bash -c "source /opt/ros/humble/setup.bash && source /home/atiravs/ros2_humble_ws/install/setup.bash && rqt --force-discover"
```

Then in RQt: **Plugins → Arduino Dashboard**

---

## Prerequisites
- Arduino connected (check port with `ls /dev/ttyACM*`)
- Arduino code uploaded with the motor control firmware
- Docker container `ros2_humble` exists

---

## 🔧 TROUBLESHOOTING

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

### Problem: Arduino not detected
```bash
# Check Arduino port on HOST (not in container)
ls -la /dev/ttyACM*

# Check if visible in container
docker exec ros2_humble ls -la /dev/ttyACM*

# If port changed, may need to restart container
docker stop ros2_humble && docker start ros2_humble
```

### Problem: Permission denied on serial port
```bash
docker exec ros2_humble chmod 666 /dev/ttyACM1
```

### Problem: pyserial not installed
```bash
docker exec ros2_humble apt-get update
docker exec ros2_humble apt-get install -y python3-serial
```

### Problem: Serial node won't connect
```bash
# Kill any existing serial nodes
docker exec ros2_humble pkill -f serial_node

# Test serial directly
docker exec ros2_humble bash -c 'python3 -c "import serial; s=serial.Serial(\"/dev/ttyACM1\", 115200, timeout=1); print(s.readline()); s.close()"'

# Restart serial node
docker exec -d ros2_humble bash -c "source /opt/ros/humble/setup.bash && source /home/atiravs/ros2_humble_ws/install/setup.bash && ros2 run arduino_dashboard serial_node"
```

### Problem: GUI not showing sensor data
```bash
# Check if serial node is running
docker exec ros2_humble bash -c "source /opt/ros/humble/setup.bash && ros2 node list"

# Enable GUI mode manually
docker exec ros2_humble bash -c "source /opt/ros/humble/setup.bash && ros2 topic pub /arduino/cmd/gui_on std_msgs/msg/Bool '{data: true}' --once"

# Check sensor data is flowing
docker exec ros2_humble bash -c "source /opt/ros/humble/setup.bash && timeout 3 ros2 topic echo /arduino/potentiometer"
```

### Problem: Package not built
```bash
docker exec ros2_humble bash -c "source /opt/ros/humble/setup.bash && cd /home/atiravs/ros2_humble_ws && colcon build --packages-select arduino_dashboard --symlink-install"
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

### Test DC motor (50% speed):
```bash
docker exec ros2_humble bash -c "source /opt/ros/humble/setup.bash && ros2 topic pub /arduino/cmd/dc_vel std_msgs/msg/Int32 '{data: 128}' --once"
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

| Topic | Type | Description |
|-------|------|-------------|
| `/arduino/cmd/servo` | Int32 | Servo angle (0-180) |
| `/arduino/cmd/step` | Int32 | Stepper position |
| `/arduino/cmd/dc_vel` | Int32 | DC motor PWM (0-255) |
| `/arduino/cmd/dc_dir` | Bool | DC direction (false=CW, true=CCW) |
| `/arduino/cmd/gui_on` | Bool | Enable/disable GUI mode |
| `/arduino/cmd/control_mode` | Int32 | Control mode (1=GUI, 2=Sensor) |
| `/arduino/potentiometer` | Int32 | Potentiometer value (0-1023) |
| `/arduino/fsr` | Int32 | FSR sensor value (0-1023) |
| `/arduino/encoder` | Int32 | Encoder position |
| `/arduino/state` | Int32 | Current control mode (1 or 2) |

---

## 🎮 TWO-STATE CONTROL SYSTEM

### State 1: GUI Control Mode (Default)
- **Servo**: Controlled by SERVO slider (0-180°)
- **DC Motor**: Controlled by DC VEL slider (0-255 PWM) and direction toggle
- **Stepper**: Controlled by STEP slider

### State 2: Sensor-Based Autonomous Mode
- **Servo**: Controlled by potentiometer (maps to 10-170°)
- **DC Motor**: Controlled by potentiometer (maps to 100-250 PWM)
- **Stepper**: Controlled by FSR (pressure controls step speed)
- **Button**: Toggles DC motor direction when pressed

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

## Arduino Command Protocol

| Command | Description | Example |
|---------|-------------|---------|
| `Vxxx` | Servo angle (0-180) | `V90` = 90 degrees |
| `Txxx` | Stepper position | `T100` = position 100 |
| `Pxxx` | DC motor PWM (0-255) | `P128` = 50% speed |
| `Dx` | DC direction (0=CW, 1=CCW) | `D1` = counter-clockwise |
| `Gx` | GUI enable (0=off, 1=on) | `G1` = enable |
| `Cx` | Control mode (1=GUI, 2=Sensor) | `C2` = sensor mode |

## Arduino Response Protocol

| Response | Description | Example |
|----------|-------------|---------|
| `Pxxx` | Potentiometer value | `P512` = mid position |
| `Fxxx` | FSR sensor value | `F100` = light pressure |
| `Exxx` | Encoder position | `E45` = position 45 |
| `Xx` | Current control mode | `X1` = GUI mode, `X2` = Sensor mode |
