# Arduino Dashboard

RQt dashboard for monitoring Arduino sensors and motor states via ROS2.

## Overview

This package provides:
- A serial communication node that reads data from an Arduino
- An RQt plugin dashboard displaying sensor readings and motor states

## Installation

1. Clone this package into your ROS2 workspace:
   ```bash
   cd ~/ros2_humble_ws/src
   # Copy or clone the arduino_dashboard package here
   ```

2. Install dependencies:
   ```bash
   pip install pyserial
   ```

3. Build the workspace:
   ```bash
   cd ~/ros2_humble_ws
   colcon build --packages-select arduino_dashboard
   source install/setup.bash
   ```

## Usage

### Launch Everything
```bash
ros2 launch arduino_dashboard dashboard.launch.py
```

### Run Components Separately

1. Start the serial node:
   ```bash
   ros2 run arduino_dashboard serial_node --ros-args -p port:=/dev/ttyACM0 -p baudrate:=115200
   ```

2. Start the RQt dashboard:
   ```bash
   rqt --standalone arduino_dashboard
   ```

## Arduino Data Format

The serial node expects data from the Arduino in the following format:
```
S1:123.45,S2:67.89,S3:234.56,M1:1,M2:0,M3:1
```

Where:
- `S1`, `S2`, `S3` - Sensor values (float)
- `M1`, `M2`, `M3` - Motor states (0 or 1)

## Topics

### Published Topics
- `/arduino/sensor1` (std_msgs/Float32) - Sensor 1 reading
- `/arduino/sensor2` (std_msgs/Float32) - Sensor 2 reading
- `/arduino/sensor3` (std_msgs/Float32) - Sensor 3 reading
- `/arduino/motor1` (std_msgs/Bool) - Motor 1 state
- `/arduino/motor2` (std_msgs/Bool) - Motor 2 state
- `/arduino/motor3` (std_msgs/Bool) - Motor 3 state

## Parameters

- `port` (string, default: `/dev/ttyACM0`) - Serial port for Arduino
- `baudrate` (int, default: `115200`) - Serial baud rate

## License

MIT
