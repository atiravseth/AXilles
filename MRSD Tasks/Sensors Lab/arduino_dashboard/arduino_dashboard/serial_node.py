#!/usr/bin/env python3
"""
Arduino Serial Node for ROS2 Dashboard

Command Format (sent to Arduino): Single letter followed by a number
  V90  - Set servo to 90 degrees
  T400 - Set stepper position (0-720 maps to -360 to 360)
  P128 - Set DC motor PWM/velocity (0-255) - velocity mode
  M500 - Set DC motor target position - position mode
  D1   - Set DC motor direction to CCW
  D0   - Set DC motor direction to CW
  G1   - GUI control enabled
  G0   - GUI control disabled
  C1   - Control mode 1 (GUI control)
  C2   - Control mode 2 (Sensor-based autonomous)
  Qvelocity   - DC control mode: velocity
  Qposition   - DC control mode: position

Sensor Format (received from Arduino): Letter followed by value
  P512 - Potentiometer reading = 512
  F200 - FSR sensor reading = 200
  E45  - Encoder position = 45
  X1   - Current state/mode = 1
"""
import rclpy
from rclpy.node import Node
from std_msgs.msg import Int32, Bool, Float32, String
import serial
import serial.tools.list_ports
import threading


class ArduinoSerialNode(Node):
    def __init__(self):
        super().__init__('arduino_serial_node')
        
        # Parameters
        self.declare_parameter('port', '/dev/ttyUSB0')
        self.declare_parameter('baudrate', 115200)
        self.declare_parameter('simulation', False)
        
        port = self.get_parameter('port').value
        baudrate = self.get_parameter('baudrate').value
        self._simulation = self.get_parameter('simulation').value
        
        self.ser = None
        self.connected = False
        
        if not self._simulation:
            # Try to connect to Arduino
            try:
                self.ser = serial.Serial(port, baudrate, timeout=0.1)
                self.connected = True
                self.get_logger().info(f'Connected to Arduino on {port}')
            except serial.SerialException:
                self.get_logger().error(f'Failed to connect to {port}')
                self.find_arduino()
        else:
            self.get_logger().info('Running in simulation mode')
        
        # Current command values
        self._servo_cmd = 0
        self._step_cmd = 0
        self._dc_vel_cmd = 0
        self._dc_pos_cmd = 0
        self._dc_dir_ccw = False  # False = CW, True = CCW
        self._gui_on = False
        self._control_mode = 1  # 1 = GUI, 2 = Sensor-based autonomous
        self._dc_control_mode = 'velocity'  # 'velocity' or 'position'  # ADDED
        
        # Track last sent values to avoid redundant sends
        self._last_servo = -1
        self._last_step = -1
        self._last_dc_vel = -1
        self._last_dc_pos = -1  # ADDED
        self._last_dc_dir = None
        self._last_dc_control_mode = None  # ADDED
        
        # Simulation state
        self._sim_state = 0
        self._sim_sensor = 0.0
        
        # Publishers for state and sensor data (from Arduino)
        self.state_pub = self.create_publisher(Int32, '/arduino/state', 10)
        self.sensor_pub = self.create_publisher(Float32, '/arduino/sensor', 10)
        self.potentiometer_pub = self.create_publisher(Int32, '/arduino/pot', 10)
        self.fsr_pub = self.create_publisher(Int32, '/arduino/fsr', 10)
        self.encoder_pub = self.create_publisher(Int32, '/arduino/encoder', 10)
        
        # Subscribers for commands (from GUI)
        self.create_subscription(Int32, '/arduino/cmd/servo', self._servo_callback, 10)
        self.create_subscription(Int32, '/arduino/cmd/step', self._step_callback, 10)
        self.create_subscription(Int32, '/arduino/cmd/dc_vel', self._dc_vel_callback, 10)
        self.create_subscription(Int32, '/arduino/cmd/dc_pos', self._dc_pos_callback, 10)
        self.create_subscription(Bool, '/arduino/cmd/dc_dir', self._dc_dir_callback, 10)
        self.create_subscription(Bool, '/arduino/cmd/gui_on', self._gui_on_callback, 10)
        self.create_subscription(Int32, '/arduino/cmd/control_mode', self._control_mode_callback, 10)
        self.create_subscription(String, '/arduino/cmd/dc_control_mode', self._dc_control_mode_callback, 10)  # ADDED
        
        # Timer to read serial data
        self.read_timer = self.create_timer(0.05, self.read_serial)  # 20 Hz
        
        # Lock for thread safety
        self._lock = threading.Lock()
        
    def find_arduino(self):
        """Auto-detect Arduino port"""
        ports = serial.tools.list_ports.comports()
        for port in ports:
            if 'Arduino' in port.description or 'USB' in port.description or 'ACM' in port.device:
                try:
                    self.ser = serial.Serial(port.device, 115200, timeout=0.1)
                    self.connected = True
                    self.get_logger().info(f'Auto-detected Arduino on {port.device}')
                    return
                except serial.SerialException:
                    continue
        self.get_logger().warn('No Arduino found! Running in simulation mode.')
        self._simulation = True
        self.connected = False
    
    def _send_command(self, cmd):
        """Send a single command to Arduino"""
        if self.connected and self.ser:
            try:
                self.ser.write(f"{cmd}\n".encode('utf-8'))
                self.get_logger().debug(f'Sent: {cmd}')
            except Exception as e:
                self.get_logger().warn(f'Serial write error: {e}')
        elif self._simulation:
            self.get_logger().debug(f'[SIM] Would send: {cmd}')
        
    def _servo_callback(self, msg):
        """Handle servo command - sends V followed by angle (0-180)"""
        with self._lock:
            self._servo_cmd = msg.data
            if self._gui_on and self._servo_cmd != self._last_servo:
                self._send_command(f"V{self._servo_cmd}")
                self._last_servo = self._servo_cmd
        self.get_logger().debug(f'Servo command: {msg.data}')
        
    def _step_callback(self, msg):
        """Handle step command - sends T followed by mapped position (0-720)"""
        with self._lock:
            self._step_cmd = msg.data
            if self._gui_on and self._step_cmd != self._last_step:
                mapped_value = self._step_cmd
                if mapped_value < 0:
                    mapped_value = 721 + mapped_value
                self._send_command(f"T{mapped_value}")
                self._last_step = self._step_cmd
        self.get_logger().debug(f'Step command: {msg.data}')
        
    def _dc_vel_callback(self, msg):
        """Handle DC velocity command - sends P followed by PWM value (0-255)"""
        with self._lock:
            self._dc_vel_cmd = msg.data
            # Only send if in velocity mode
            if self._gui_on and self._dc_control_mode == 'velocity' and self._dc_vel_cmd != self._last_dc_vel:
                self._send_command(f"P{self._dc_vel_cmd}")
                self._last_dc_vel = self._dc_vel_cmd
        self.get_logger().debug(f'DC Vel command: {msg.data}')
        
    def _dc_pos_callback(self, msg):
        """Handle DC position command - sends M followed by target position"""
        with self._lock:
            self._dc_pos_cmd = msg.data
            # Only send if in position mode
            if self._gui_on and self._dc_control_mode == 'position' and self._dc_pos_cmd != self._last_dc_pos:
                self._send_command(f"M{self._dc_pos_cmd}")
                self._last_dc_pos = self._dc_pos_cmd
        self.get_logger().debug(f'DC Pos command: {msg.data}')
        
    def _dc_dir_callback(self, msg):
        """Handle DC direction command - sends D0 for CW, D1 for CCW"""
        with self._lock:
            self._dc_dir_ccw = msg.data
            if self._gui_on and self._dc_dir_ccw != self._last_dc_dir:
                dir_val = 1 if self._dc_dir_ccw else 0
                self._send_command(f"D{dir_val}")
                self._last_dc_dir = self._dc_dir_ccw
        self.get_logger().debug(f'DC Dir command: {"CCW" if msg.data else "CW"}')
    
    # ==================== ADDED: DC CONTROL MODE CALLBACK ====================
    def _dc_control_mode_callback(self, msg):
        """Handle DC control mode command - sends Qvelocity or Qposition"""
        with self._lock:
            mode = msg.data.lower()
            if mode in ['velocity', 'position']:
                self._dc_control_mode = mode
                
                if self._gui_on and self._dc_control_mode != self._last_dc_control_mode:
                    self._send_command(f"Q{mode}")
                    self._last_dc_control_mode = self._dc_control_mode
                    
                    # Reset tracking for position/velocity
                    self._last_dc_vel = -1
                    self._last_dc_pos = -1
                    
                self.get_logger().info(f'DC Control Mode: {mode}')
    # ==================== END DC CONTROL MODE CALLBACK ====================
        
    def _gui_on_callback(self, msg):
        """Handle GUI ON command - sends G1 to enable, G0 to disable"""
        with self._lock:
            self._gui_on = msg.data
            gui_val = 1 if self._gui_on else 0
            self._send_command(f"G{gui_val}")
            
            # Reset tracking when GUI state changes
            if self._gui_on:
                self._last_servo = -1
                self._last_step = -1
                self._last_dc_vel = -1
                self._last_dc_pos = -1
                self._last_dc_dir = None
                self._last_dc_control_mode = None
                
        self.get_logger().info(f'GUI ON: {msg.data}')
    
    def _control_mode_callback(self, msg):
        """Handle control mode command - sends C1 for GUI mode, C2 for sensor mode"""
        with self._lock:
            self._control_mode = msg.data
            self._send_command(f"C{self._control_mode}")
        self.get_logger().info(f'Control Mode: {self._control_mode} ({"GUI" if self._control_mode == 1 else "Sensor-Based Autonomous"})')
        
    def read_serial(self):
        """Read and parse data from Arduino"""
        if self.connected and self.ser:
            try:
                while self.ser.in_waiting > 0:
                    line = self.ser.readline().decode('utf-8').strip()
                    if line:
                        self.parse_data(line)
            except Exception as e:
                self.get_logger().warn(f'Serial read error: {e}')
        elif self._simulation:
            # Simulation mode - publish simulated data
            self._publish_simulation_data()
            
    def _publish_simulation_data(self):
        """Publish simulation data for testing without Arduino"""
        import math
        import time
        
        # Generate smooth changing values based on time
        t = time.time()
        
        # State cycles through 0-5
        self._sim_state = int(t) % 6
        
        # Sensor value oscillates (simulating potentiometer 0-4095)
        self._sim_sensor = 2048.0 + 1500.0 * math.sin(t * 0.5)
        
        # Publish state
        state_msg = Int32()
        state_msg.data = self._sim_state
        self.state_pub.publish(state_msg)
        
        # Publish sensor value (as potentiometer)
        sensor_msg = Float32()
        sensor_msg.data = self._sim_sensor
        self.sensor_pub.publish(sensor_msg)
        
        # Publish potentiometer (0-4095 for ESP32)
        pot_msg = Int32()
        pot_msg.data = int(self._sim_sensor)
        self.potentiometer_pub.publish(pot_msg)
        
        # Publish FSR (simulated, 0-4095 for ESP32)
        fsr_value = 1000 + 800 * math.sin(t * 0.3)
        fsr_msg = Int32()
        fsr_msg.data = int(fsr_value)
        self.fsr_pub.publish(fsr_msg)
        
        # Publish encoder (simulated)
        encoder_value = int(100 * math.sin(t * 0.2))
        enc_msg = Int32()
        enc_msg.data = encoder_value
        self.encoder_pub.publish(enc_msg)
            
    def parse_data(self, line):
        """
        Parse Arduino data in letter+value format
        
        Expected formats:
          P512 - Potentiometer reading (0-4095 for ESP32)
          F200 - FSR sensor reading (0-4095 for ESP32)
          E45  - Encoder position
          X1   - Current state
        """
        try:
            if len(line) < 2:
                return
                
            cmd_type = line[0].upper()
            value_str = line[1:]
            
            if cmd_type == 'P':
                # Potentiometer reading
                value = int(value_str)
                msg = Int32()
                msg.data = value
                self.potentiometer_pub.publish(msg)
                
                # Also publish to generic sensor topic
                sensor_msg = Float32()
                sensor_msg.data = float(value)
                self.sensor_pub.publish(sensor_msg)
                self.get_logger().debug(f'Potentiometer: {value}')
                
            elif cmd_type == 'F':
                # FSR sensor reading
                value = int(value_str)
                msg = Int32()
                msg.data = value
                self.fsr_pub.publish(msg)
                self.get_logger().debug(f'FSR: {value}')
                
            elif cmd_type == 'E':
                # Encoder position
                value = int(value_str)
                msg = Int32()
                msg.data = value
                self.encoder_pub.publish(msg)
                self.get_logger().debug(f'Encoder: {value}')
                
            elif cmd_type == 'X':
                # State
                value = int(value_str)
                msg = Int32()
                msg.data = value
                self.state_pub.publish(msg)
                self.get_logger().debug(f'State: {value}')
                
            else:
                # Unknown command type, log as debug message
                self.get_logger().debug(f'Arduino: {line}')
                
        except ValueError as e:
            self.get_logger().warn(f'Parse error: {e} - Line: {line}')
        except Exception as e:
            self.get_logger().warn(f'Unexpected error: {e} - Line: {line}')


def main(args=None):
    rclpy.init(args=args)
    node = ArduinoSerialNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        if node.ser:
            node.ser.close()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()