#!/usr/bin/env python3
import os
import rclpy
from rclpy.node import Node
from rqt_gui_py.plugin import Plugin
from python_qt_binding.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
    QSlider, QCheckBox, QPushButton, QFrame, QGridLayout,
    QGroupBox, QSpinBox, QProgressBar, QRadioButton, QButtonGroup
)
from python_qt_binding.QtCore import Qt, QTimer, Signal, QObject
from python_qt_binding.QtGui import QFont, QPalette, QColor, QPainter, QBrush, QPen
from std_msgs.msg import Int32, Bool, String, Float32, Int32MultiArray

class SignalBridge(QObject):
    """Bridge for thread-safe signal emission"""
    state_changed = Signal(int)
    sensor_changed = Signal(float)
    pot_changed = Signal(int)
    fsr_changed = Signal(int)
    encoder_changed = Signal(int)
    tof_changed = Signal(list)


class TofDepthWidget(QWidget):
    """Widget to display 8x8 TOF depth data as a point cloud visualization"""
    def __init__(self, parent=None):
        super(TofDepthWidget, self).__init__(parent)
        self.setMinimumSize(160, 160)
        self.setMaximumSize(200, 200)
        self._data = [0] * 64  # 8x8 depth values in mm
        self._max_distance = 4000  # Max distance in mm for scaling
        self._min_radius = 2
        self._max_radius = 12
        
    def setData(self, data):
        """Set the 8x8 depth data (64 values)"""
        if len(data) == 64:
            self._data = data
            self.update()
    
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        # Black background
        painter.fillRect(self.rect(), QColor(0, 0, 0))
        
        # Calculate cell size
        w = self.width()
        h = self.height()
        cell_w = w / 8
        cell_h = h / 8
        
        # Cyan color for dots
        cyan = QColor(0, 255, 255)
        painter.setPen(Qt.NoPen)
        
        for i in range(8):
            for j in range(8):
                idx = i * 8 + j
                distance = self._data[idx]
                
                # Map distance to radius (small distance = large radius)
                if distance <= 0 or distance > self._max_distance:
                    radius = self._min_radius
                else:
                    # Inverse mapping: closer = larger radius
                    ratio = 1.0 - (distance / self._max_distance)
                    radius = self._min_radius + ratio * (self._max_radius - self._min_radius)
                
                # Draw the dot
                cx = j * cell_w + cell_w / 2
                cy = i * cell_h + cell_h / 2
                
                painter.setBrush(QBrush(cyan))
                painter.drawEllipse(int(cx - radius), int(cy - radius), 
                                   int(radius * 2), int(radius * 2))
        
        painter.end()

class ArduinoDashboard(Plugin):
    def __init__(self, context):
        super(ArduinoDashboard, self).__init__(context)
        self.setObjectName('ArduinoDashboard')
        
        # Create the main widget
        self._widget = QWidget()
        self._widget.setWindowTitle('Arduino Control Panel')
        
        # Initialize ROS2 node (rqt handles the context)
        if not rclpy.ok():
            rclpy.init()
        self._node = context.node
        
        # Signal bridge for thread-safe updates
        self._signals = SignalBridge()
        self._signals.state_changed.connect(self._on_state_changed)
        self._signals.sensor_changed.connect(self._on_sensor_changed)
        self._signals.pot_changed.connect(self._on_pot_changed)
        self._signals.fsr_changed.connect(self._on_fsr_changed)
        self._signals.encoder_changed.connect(self._on_encoder_changed)
        self._signals.tof_changed.connect(self._on_tof_changed)
        
        # Current values
        self._servo_value = 0
        self._step_value = 0
        self._dc_vel_value = 0
        self._dc_pos_value = 0
        self._dc_direction_cw = True  # True = CW, False = CCW
        self._dc_control_mode = 'velocity'  # 'velocity' or 'position'  # NEW
        self._gui_on = False
        self._current_state = 0
        self._sensor_value = 0.0
        self._pot_value = 0
        self._fsr_value = 0
        self._encoder_value = 0
        self._tof_data = [0] * 64  # TOF 8x8 data
        self._control_mode = 1  # 1 = GUI Control, 2 = Sensor-Based Autonomous
        self._fsr_level = None
        self._last_state = None
        
        # Build the UI
        self._create_ui()
        
        # Create publishers and subscribers
        self._create_ros_interfaces()
        
        # Timer to process ROS callbacks
        self._update_timer = QTimer()
        self._update_timer.timeout.connect(self._update_ros)
        self._update_timer.start(50)  # 20 Hz
        
        # Add widget to the user interface
        context.add_widget(self._widget)
        
    def _create_ui(self):
        """Create the dashboard UI with a modern card-based layout"""
        main_layout = QHBoxLayout()
        main_layout.setSpacing(12)
        main_layout.setContentsMargins(15, 15, 15, 15)
        
        # Set modern light theme with blue accents
        self._widget.setStyleSheet("""
            QWidget {
                background-color: #f0f4f8;
                color: #1a365d;
                font-family: 'Segoe UI', Arial, sans-serif;
            }
            QGroupBox {
                background-color: white;
                border: 1px solid #e2e8f0;
                border-radius: 12px;
                margin-top: 12px;
                padding: 15px;
                font-size: 13px;
                font-weight: bold;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 15px;
                padding: 0 8px;
                color: #2b6cb0;
            }
            QLabel {
                color: #2d3748;
                font-size: 12px;
            }
            QSlider::groove:vertical {
                border: none;
                width: 8px;
                background: #e2e8f0;
                border-radius: 4px;
            }
            QSlider::handle:vertical {
                background: #3182ce;
                border: none;
                height: 20px;
                width: 20px;
                margin: 0 -6px;
                border-radius: 10px;
            }
            QSlider::sub-page:vertical {
                background: #e2e8f0;
                border-radius: 4px;
            }
            QSlider::add-page:vertical {
                background: qlineargradient(y1:0, y2:1, stop:0 #3182ce, stop:1 #63b3ed);
                border-radius: 4px;
            }
            QSpinBox {
                background-color: #edf2f7;
                border: 2px solid #cbd5e0;
                border-radius: 6px;
                padding: 5px 10px;
                font-size: 14px;
                font-weight: bold;
                color: #2d3748;
            }
            QSpinBox::up-button, QSpinBox::down-button {
                width: 0;
                height: 0;
            }
            QCheckBox {
                color: #2d3748;
                font-size: 12px;
                spacing: 8px;
            }
            QCheckBox::indicator {
                width: 22px;
                height: 22px;
                border: 2px solid #a0aec0;
                border-radius: 6px;
                background: white;
            }
            QCheckBox::indicator:checked {
                background: #48bb78;
                border-color: #48bb78;
            }
            QRadioButton {
                color: #2d3748;
                font-size: 12px;
                spacing: 8px;
            }
            QRadioButton::indicator {
                width: 18px;
                height: 18px;
                border: 2px solid #a0aec0;
                border-radius: 9px;
                background: white;
            }
            QRadioButton::indicator:checked {
                background: #4299e1;
                border-color: #4299e1;
            }
            QPushButton {
                background-color: #4299e1;
                color: white;
                border: none;
                border-radius: 8px;
                padding: 12px 24px;
                font-size: 13px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #3182ce;
            }
            QPushButton:pressed {
                background-color: #2b6cb0;
            }
            QPushButton:checked {
                background-color: #48bb78;
            }
            QProgressBar {
                border: none;
                border-radius: 6px;
                background-color: #e2e8f0;
                height: 12px;
                text-align: center;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, x2:1, stop:0 #4299e1, stop:1 #63b3ed);
                border-radius: 6px;
            }
        """)
        
        # LEFT COLUMN - Motor Controls
        left_column = QVBoxLayout()
        left_column.setSpacing(10)
        
        # Title
        title_label = QLabel("⚡ MOTOR CONTROL")
        title_label.setStyleSheet("font-size: 16px; font-weight: bold; color: #2b6cb0; padding: 5px;")
        left_column.addWidget(title_label)
        
        # Motor sliders in a grid (vertical sliders)
        motors_group = QGroupBox("Actuators")
        motors_layout = QHBoxLayout()
        motors_layout.setSpacing(25)
        
        # Create vertical sliders for each motor
        motor_configs = [
            ("SERVO", 0, 180, "°"),
            ("STEP", 0, 360, "°"),  # Degrees: 360° = 1600 steps
            ("DC VEL", 0, 103, "RPM"),  # RPM display, converted to PWM (0-255) internally
            ("DC POS", 0, 1620, "°")  # Degrees: 1620° = 450 encoder values
        ]
        
        self._sliders = []
        self._spinboxes = []
        
        for name, min_val, max_val, unit in motor_configs:
            motor_widget = QVBoxLayout()
            motor_widget.setAlignment(Qt.AlignCenter)
            
            # Label at top
            label = QLabel(name)
            label.setStyleSheet("font-weight: bold; color: #4a5568;")
            label.setAlignment(Qt.AlignCenter)
            motor_widget.addWidget(label)
            
            # Vertical slider
            slider = QSlider(Qt.Vertical)
            slider.setMinimum(min_val)
            slider.setMaximum(max_val)
            slider.setValue(0)
            slider.setFixedHeight(120)
            slider.setFixedWidth(30)
            motor_widget.addWidget(slider, alignment=Qt.AlignCenter)
            
            # Spinbox for value
            spinbox = QSpinBox()
            spinbox.setMinimum(min_val)
            spinbox.setMaximum(max_val)
            spinbox.setValue(0)
            spinbox.setFixedWidth(70)
            spinbox.setAlignment(Qt.AlignCenter)
            motor_widget.addWidget(spinbox, alignment=Qt.AlignCenter)
            
            # Connect slider and spinbox
            slider.valueChanged.connect(spinbox.setValue)
            spinbox.valueChanged.connect(slider.setValue)
            
            self._sliders.append(slider)
            self._spinboxes.append(spinbox)
            
            motors_layout.addLayout(motor_widget)
        
        # Connect to callbacks
        self._sliders[0].valueChanged.connect(self._on_servo_changed)
        self._sliders[1].valueChanged.connect(self._on_step_changed)
        self._sliders[2].valueChanged.connect(self._on_dc_vel_changed)
        self._sliders[3].valueChanged.connect(self._on_dc_pos_changed)
        
        motors_group.setLayout(motors_layout)
        left_column.addWidget(motors_group)
        
        # ==================== NEW: DC MOTOR CONTROL MODE ====================
        dc_mode_group = QGroupBox("DC Motor Control Mode")
        dc_mode_layout = QVBoxLayout()
        
        # Radio buttons for velocity vs position
        self._dc_velocity_radio = QRadioButton("⚡ Velocity Control")
        self._dc_velocity_radio.setChecked(True)
        self._dc_velocity_radio.toggled.connect(self._on_dc_mode_changed)
        
        self._dc_position_radio = QRadioButton("📍 Position Control (Encoder)")
        
        # Button group to make them mutually exclusive
        self._dc_mode_button_group = QButtonGroup()
        self._dc_mode_button_group.addButton(self._dc_velocity_radio)
        self._dc_mode_button_group.addButton(self._dc_position_radio)
        
        dc_mode_layout.addWidget(self._dc_velocity_radio)
        dc_mode_layout.addWidget(self._dc_position_radio)
        
        # Info label
        self._dc_mode_info = QLabel("VEL: Closed-loop velocity (°/s)")
        self._dc_mode_info.setStyleSheet("color: #718096; font-size: 10px; padding: 5px;")
        self._dc_mode_info.setWordWrap(True)
        dc_mode_layout.addWidget(self._dc_mode_info)
        
        dc_mode_group.setLayout(dc_mode_layout)
        left_column.addWidget(dc_mode_group)
        # ==================== END DC MOTOR CONTROL MODE ====================
        
        # DC Direction toggle
        dir_group = QGroupBox("DC Motor Direction")
        dir_layout = QHBoxLayout()
        
        self._cw_label = QLabel("CW")
        self._cw_label.setStyleSheet("font-weight: bold; color: #48bb78;")
        
        self._dc_dir_checkbox = QCheckBox()
        self._dc_dir_checkbox.setFixedSize(50, 26)
        self._dc_dir_checkbox.stateChanged.connect(self._on_dc_dir_changed)
        
        self._ccw_label = QLabel("CCW")
        self._ccw_label.setStyleSheet("font-weight: bold; color: #a0aec0;")
        
        dir_layout.addStretch()
        dir_layout.addWidget(self._cw_label)
        dir_layout.addWidget(self._dc_dir_checkbox)
        dir_layout.addWidget(self._ccw_label)
        dir_layout.addStretch()
        
        dir_group.setLayout(dir_layout)
        left_column.addWidget(dir_group)
        
        left_column.addStretch()
        
        # RIGHT COLUMN - Status & Control
        right_column = QVBoxLayout()
        right_column.setSpacing(10)
        
        # Connection control
        conn_group = QGroupBox("Connection")
        conn_layout = QVBoxLayout()
        
        self._gui_on_button = QPushButton("🔌 CONNECT")
        self._gui_on_button.setCheckable(True)
        self._gui_on_button.setFixedHeight(50)
        self._gui_on_button.clicked.connect(self._on_gui_on_clicked)
        conn_layout.addWidget(self._gui_on_button)
        
        # Connection status indicator
        self._status_frame = QFrame()
        self._status_frame.setFixedHeight(8)
        self._status_frame.setStyleSheet("background-color: #fc8181; border-radius: 4px;")
        conn_layout.addWidget(self._status_frame)
        
        conn_group.setLayout(conn_layout)
        right_column.addWidget(conn_group)
        
        # Control Mode selection
        mode_group = QGroupBox("Control Mode")
        mode_layout = QVBoxLayout()
        
        # Mode 1 button - GUI Control
        self._mode1_button = QPushButton("🎮 GUI CONTROL")
        self._mode1_button.setCheckable(True)
        self._mode1_button.setChecked(True)
        self._mode1_button.setFixedHeight(40)
        self._mode1_button.clicked.connect(lambda: self._on_mode_changed(1))
        mode_layout.addWidget(self._mode1_button)
        
        # Mode 2 button - Sensor Autonomous
        self._mode2_button = QPushButton("🤖 SENSOR AUTONOMOUS")
        self._mode2_button.setCheckable(True)
        self._mode2_button.setChecked(False)
        self._mode2_button.setFixedHeight(40)
        self._mode2_button.clicked.connect(lambda: self._on_mode_changed(2))
        mode_layout.addWidget(self._mode2_button)
        
        # Mode description
        self._mode_desc = QLabel("GUI: Sliders control motors")
        self._mode_desc.setStyleSheet("color: #718096; font-size: 10px; padding: 5px;")
        self._mode_desc.setAlignment(Qt.AlignCenter)
        self._mode_desc.setWordWrap(True)
        mode_layout.addWidget(self._mode_desc)
        
        mode_group.setLayout(mode_layout)
        right_column.addWidget(mode_group)
        
        # State display
        state_group = QGroupBox("Arduino State")
        state_layout = QVBoxLayout()
        
        self._state_label = QLabel("0")
        self._state_label.setStyleSheet("""
            font-size: 48px; 
            font-weight: bold; 
            color: #2b6cb0;
            background-color: #ebf8ff;
            border-radius: 10px;
            padding: 15px;
        """)
        self._state_label.setAlignment(Qt.AlignCenter)
        state_layout.addWidget(self._state_label)
        
        state_desc = QLabel("Current State ID")
        state_desc.setStyleSheet("color: #718096; font-size: 11px;")
        state_desc.setAlignment(Qt.AlignCenter)
        state_layout.addWidget(state_desc)
        
        state_group.setLayout(state_layout)
        right_column.addWidget(state_group)
        
        # Sensor readings group
        sensors_group = QGroupBox("📊 Sensor Readings")
        sensors_layout = QVBoxLayout()
        sensors_layout.setSpacing(8)
        
        # Potentiometer reading
        pot_layout = QHBoxLayout()
        pot_icon = QLabel("🎚️")
        pot_icon.setStyleSheet("font-size: 20px;")
        pot_label = QLabel("POT:")
        pot_label.setStyleSheet("font-weight: bold; color: #4a5568;")
        self._pot_value_label = QLabel("0")
        self._pot_value_label.setStyleSheet("""
            font-size: 18px; 
            font-weight: bold; 
            color: #2b6cb0;
            background-color: #ebf8ff;
            border-radius: 6px;
            padding: 5px 10px;
        """)
        self._pot_value_label.setMinimumWidth(80)
        self._pot_value_label.setAlignment(Qt.AlignCenter)
        pot_layout.addWidget(pot_icon)
        pot_layout.addWidget(pot_label)
        pot_layout.addStretch()
        pot_layout.addWidget(self._pot_value_label)
        sensors_layout.addLayout(pot_layout)
        
        # Potentiometer progress bar
        self._pot_bar = QProgressBar()
        self._pot_bar.setMinimum(0)
        self._pot_bar.setMaximum(9370)  # Updated range: 0-9370
        self._pot_bar.setValue(0)
        self._pot_bar.setTextVisible(False)
        self._pot_bar.setFixedHeight(8)
        sensors_layout.addWidget(self._pot_bar)
        
        # FSR reading
        fsr_layout = QHBoxLayout()
        fsr_icon = QLabel("👆")
        fsr_icon.setStyleSheet("font-size: 20px;")
        fsr_label = QLabel("FSR:")
        fsr_label.setStyleSheet("font-weight: bold; color: #4a5568;")
        self._fsr_value_label = QLabel("0")
        self._fsr_value_label.setStyleSheet("""
            font-size: 18px; 
            font-weight: bold; 
            color: #d69e2e;
            background-color: #fefcbf;
            border-radius: 6px;
            padding: 5px 10px;
        """)
        self._fsr_value_label.setMinimumWidth(80)
        self._fsr_value_label.setAlignment(Qt.AlignCenter)
        fsr_layout.addWidget(fsr_icon)
        fsr_layout.addWidget(fsr_label)
        fsr_layout.addStretch()
        fsr_layout.addWidget(self._fsr_value_label)
        sensors_layout.addLayout(fsr_layout)
        
        # FSR progress bar
        self._fsr_bar = QProgressBar()
        self._fsr_bar.setMinimum(0)
        self._fsr_bar.setMaximum(981)  # Updated range: 0-981
        self._fsr_bar.setValue(0)
        self._fsr_bar.setTextVisible(False)
        self._fsr_bar.setFixedHeight(8)
        self._fsr_bar.setStyleSheet("""
            QProgressBar {
                border: none;
                border-radius: 4px;
                background-color: #fef5e7;
                height: 8px;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, x2:1, stop:0 #f6ad55, stop:1 #ed8936);
                border-radius: 4px;
            }
        """)
        sensors_layout.addWidget(self._fsr_bar)
        
        # Encoder reading
        enc_layout = QHBoxLayout()
        enc_icon = QLabel("⚙️")
        enc_icon.setStyleSheet("font-size: 20px;")
        enc_label = QLabel("ENC:")
        enc_label.setStyleSheet("font-weight: bold; color: #4a5568;")
        self._encoder_value_label = QLabel("0")
        self._encoder_value_label.setStyleSheet("""
            font-size: 18px; 
            font-weight: bold; 
            color: #38a169;
            background-color: #f0fff4;
            border-radius: 6px;
            padding: 5px 10px;
        """)
        self._encoder_value_label.setMinimumWidth(80)
        self._encoder_value_label.setAlignment(Qt.AlignCenter)
        enc_layout.addWidget(enc_icon)
        enc_layout.addWidget(enc_label)
        enc_layout.addStretch()
        enc_layout.addWidget(self._encoder_value_label)
        sensors_layout.addLayout(enc_layout)
        sensors_group.setLayout(sensors_layout)
        right_column.addWidget(sensors_group)
        
        # TOF Depth Visualization Widget
        tof_group = QGroupBox("📡 TOF Depth (8x8)")
        tof_layout = QVBoxLayout()
        
        self._tof_widget = TofDepthWidget()
        tof_layout.addWidget(self._tof_widget, alignment=Qt.AlignCenter)
        
        tof_info = QLabel("Cyan dots: closer = larger")
        tof_info.setStyleSheet("color: #718096; font-size: 10px;")
        tof_info.setAlignment(Qt.AlignCenter)
        tof_layout.addWidget(tof_info)
        
        tof_group.setLayout(tof_layout)
        right_column.addWidget(tof_group)
        
        right_column.addStretch()
        
        # Add columns to main layout
        main_layout.addLayout(left_column, stretch=2)
        main_layout.addLayout(right_column, stretch=1)
        
        self._widget.setLayout(main_layout)
        self._widget.setMinimumSize(550, 600)
        
    def _create_ros_interfaces(self):
        """Create ROS2 publishers and subscribers"""
        # Publishers for motor commands
        self._servo_pub = self._node.create_publisher(Int32, '/arduino/cmd/servo', 10)
        self._step_pub = self._node.create_publisher(Int32, '/arduino/cmd/step', 10)
        self._dc_vel_pub = self._node.create_publisher(Int32, '/arduino/cmd/dc_vel', 10)
        self._dc_pos_pub = self._node.create_publisher(Int32, '/arduino/cmd/dc_pos', 10)
        self._dc_dir_pub = self._node.create_publisher(Bool, '/arduino/cmd/dc_dir', 10)
        self._gui_on_pub = self._node.create_publisher(Bool, '/arduino/cmd/gui_on', 10)
        self._control_mode_pub = self._node.create_publisher(Int32, '/arduino/cmd/control_mode', 10)
        self._dc_control_mode_pub = self._node.create_publisher(String, '/arduino/cmd/dc_control_mode', 10)  # NEW
        
        # Subscribers for state and sensor data
        self._node.create_subscription(
            Int32, '/arduino/state',
            self._state_callback, 10)
        self._node.create_subscription(
            Float32, '/arduino/sensor',
            self._sensor_callback, 10)
        self._node.create_subscription(
            Int32, '/arduino/pot',
            self._pot_callback, 10)
        self._node.create_subscription(
            Int32, '/arduino/fsr',
            self._fsr_callback, 10)
        self._node.create_subscription(
            Int32, '/arduino/encoder',
            self._encoder_callback, 10)
        self._node.create_subscription(
            Int32MultiArray, '/arduino/tof',
            self._tof_callback, 10)
    
    # ==================== NEW: DC CONTROL MODE HANDLER ====================
    def _on_dc_mode_changed(self, checked):
        """Handle DC motor control mode change"""
        if self._dc_velocity_radio.isChecked():
            self._dc_control_mode = 'velocity'
            self._dc_mode_info.setText("VEL: Closed-loop velocity (°/s)")
            # Enable velocity slider, disable position slider
            self._sliders[2].setEnabled(True)
            self._spinboxes[2].setEnabled(True)
            self._sliders[3].setEnabled(False)
            self._spinboxes[3].setEnabled(False)
        else:
            self._dc_control_mode = 'position'
            self._dc_mode_info.setText("POS: Encoder feedback control")
            # Disable velocity slider, enable position slider
            self._sliders[2].setEnabled(False)
            self._spinboxes[2].setEnabled(False)
            self._sliders[3].setEnabled(True)
            self._spinboxes[3].setEnabled(True)
        
        # Publish mode change
        if self._gui_on:
            msg = String()
            msg.data = self._dc_control_mode
            self._dc_control_mode_pub.publish(msg)
            self._node.get_logger().info(f'DC Control Mode: {self._dc_control_mode}')
    # ==================== END DC CONTROL MODE HANDLER ====================
            
    def _on_servo_changed(self, value):
        """Handle servo slider change"""
        self._servo_value = value
        if self._gui_on:
            msg = Int32()
            msg.data = value
            self._servo_pub.publish(msg)
        
    def _on_step_changed(self, value):
        """Handle step slider change - converts degrees to steps (360° = 1600 steps)"""
        # Convert degrees to steps: 360 degrees = 1600 steps
        steps = int((value / 360.0) * 1600)
        self._step_value = steps
        if self._gui_on:
            msg = Int32()
            msg.data = steps
            self._step_pub.publish(msg)
        
    def _on_dc_vel_changed(self, value):
        """Handle DC velocity slider change - converts RPM (0-103) to PWM (0-255)"""
        # Convert RPM to PWM: 103 RPM = 255 PWM
        pwm_value = int((value / 103.0) * 255)
        self._dc_vel_value = pwm_value
        if self._gui_on and self._dc_control_mode == 'velocity':
            msg = Int32()
            msg.data = pwm_value
            self._dc_vel_pub.publish(msg)
        
    def _on_dc_pos_changed(self, value):
        """Handle DC position slider change - converts degrees to encoder values (1620° = 450 encoder)"""
        # Convert degrees to encoder values: 1620 degrees = 450 encoder values
        encoder_value = int((value / 1620.0) * 450)
        self._dc_pos_value = encoder_value
        if self._gui_on and self._dc_control_mode == 'position':
            msg = Int32()
            msg.data = encoder_value
            self._dc_pos_pub.publish(msg)
        
    def _on_dc_dir_changed(self, state):
        """Handle DC direction checkbox change"""
        self._dc_direction_cw = (state != Qt.Checked)
        # Update direction labels styling
        if self._dc_direction_cw:
            self._cw_label.setStyleSheet("font-weight: bold; color: #48bb78;")
            self._ccw_label.setStyleSheet("font-weight: bold; color: #a0aec0;")
        else:
            self._cw_label.setStyleSheet("font-weight: bold; color: #a0aec0;")
            self._ccw_label.setStyleSheet("font-weight: bold; color: #48bb78;")
        
        if self._gui_on:
            msg = Bool()
            msg.data = not self._dc_direction_cw  # True = CCW, False = CW
            self._dc_dir_pub.publish(msg)
    
    def _on_mode_changed(self, mode):
        """Handle control mode change"""
        self._control_mode = mode
        
        # Update button states
        self._mode1_button.setChecked(mode == 1)
        self._mode2_button.setChecked(mode == 2)
        
        # Update button styling
        if mode == 1:
            self._mode1_button.setStyleSheet("""
                QPushButton {
                    background-color: #48bb78;
                    color: white;
                    border: none;
                    border-radius: 8px;
                    padding: 10px;
                    font-size: 12px;
                    font-weight: bold;
                }
            """)
            self._mode2_button.setStyleSheet("""
                QPushButton {
                    background-color: #a0aec0;
                    color: white;
                    border: none;
                    border-radius: 8px;
                    padding: 10px;
                    font-size: 12px;
                    font-weight: bold;
                }
            """)
            self._mode_desc.setText("GUI: Sliders control motors")
            # Enable controls based on DC mode
            for i, slider in enumerate(self._sliders):
                if i == 2:  # DC VEL
                    slider.setEnabled(self._dc_control_mode == 'velocity')
                    self._spinboxes[i].setEnabled(self._dc_control_mode == 'velocity')
                elif i == 3:  # DC POS
                    slider.setEnabled(self._dc_control_mode == 'position')
                    self._spinboxes[i].setEnabled(self._dc_control_mode == 'position')
                else:
                    slider.setEnabled(True)
                    self._spinboxes[i].setEnabled(True)
        else:
            self._mode1_button.setStyleSheet("""
                QPushButton {
                    background-color: #a0aec0;
                    color: white;
                    border: none;
                    border-radius: 8px;
                    padding: 10px;
                    font-size: 12px;
                    font-weight: bold;
                }
            """)
            self._mode2_button.setStyleSheet("""
                QPushButton {
                    background-color: #805ad5;
                    color: white;
                    border: none;
                    border-radius: 8px;
                    padding: 10px;
                    font-size: 12px;
                    font-weight: bold;
                }
            """)
            self._mode_desc.setText("SENSOR: Pot→DC/Servo, FSR→Stepper")
            # Disable sliders in autonomous mode
            for slider in self._sliders:
                slider.setEnabled(False)
            for spinbox in self._spinboxes:
                spinbox.setEnabled(False)
        
        # Publish mode change
        if self._gui_on:
            msg = Int32()
            msg.data = mode
            self._control_mode_pub.publish(msg)
        
    def _on_gui_on_clicked(self):
        """Handle GUI ON button click"""
        self._gui_on = self._gui_on_button.isChecked()
        
        if self._gui_on:
            self._gui_on_button.setText("🔗 CONNECTED")
            self._gui_on_button.setStyleSheet("""
                QPushButton {
                    background-color: #48bb78;
                    color: white;
                    border: none;
                    border-radius: 8px;
                    padding: 12px 24px;
                    font-size: 13px;
                    font-weight: bold;
                }
            """)
            self._status_frame.setStyleSheet("background-color: #48bb78; border-radius: 4px;")
        else:
            self._gui_on_button.setText("🔌 CONNECT")
            self._gui_on_button.setStyleSheet("""
                QPushButton {
                    background-color: #4299e1;
                    color: white;
                    border: none;
                    border-radius: 8px;
                    padding: 12px 24px;
                    font-size: 13px;
                    font-weight: bold;
                }
            """)
            self._status_frame.setStyleSheet("background-color: #fc8181; border-radius: 4px;")
        
        # Publish GUI ON state
        msg = Bool()
        msg.data = self._gui_on
        self._gui_on_pub.publish(msg)
        
        # Send current values when GUI turns on
        if self._gui_on:
            self._send_all_values()
            
    def _send_all_values(self):
        """Send all current slider values to Arduino"""
        msg = Int32()
        
        # Send control mode first
        msg.data = self._control_mode
        self._control_mode_pub.publish(msg)
        
        # Send DC control mode
        dc_mode_msg = String()
        dc_mode_msg.data = self._dc_control_mode
        self._dc_control_mode_pub.publish(dc_mode_msg)
        
        msg.data = self._servo_value
        self._servo_pub.publish(msg)
        
        msg.data = self._step_value
        self._step_pub.publish(msg)
        
        if self._dc_control_mode == 'velocity':
            msg.data = self._dc_vel_value
            self._dc_vel_pub.publish(msg)
        else:
            msg.data = self._dc_pos_value
            self._dc_pos_pub.publish(msg)
        
        dir_msg = Bool()
        dir_msg.data = not self._dc_direction_cw
        self._dc_dir_pub.publish(dir_msg)
            
    def _state_callback(self, msg):
        """Handle state update from Arduino"""
        self._signals.state_changed.emit(msg.data)
        
    def _sensor_callback(self, msg):
        """Handle sensor update from Arduino"""
        self._signals.sensor_changed.emit(msg.data)
    
    def _pot_callback(self, msg):
        """Handle potentiometer update from Arduino"""
        self._signals.pot_changed.emit(msg.data)
    
    def _fsr_callback(self, msg):
        """Handle FSR update from Arduino"""
        self._signals.fsr_changed.emit(msg.data)
    
    def _encoder_callback(self, msg):
        """Handle encoder update from Arduino"""
        self._signals.encoder_changed.emit(msg.data)
    
    def _tof_callback(self, msg):
        """Handle TOF 8x8 depth data from Arduino"""
        self._signals.tof_changed.emit(list(msg.data))
        
    def _on_state_changed(self, state):
        """Update state label (called from main thread)"""
        if state == self._last_state:
            return
        self._last_state = state
        self._current_state = state
        if state == 1:
            self._state_label.setText("1\nGUI")
            self._state_label.setStyleSheet("""
                font-size: 32px; 
                font-weight: bold; 
                color: #2b6cb0;
                background-color: #ebf8ff;
                border-radius: 10px;
                padding: 10px;
            """)
        elif state == 2:
            self._state_label.setText("2\nAUTO")
            self._state_label.setStyleSheet("""
                font-size: 32px; 
                font-weight: bold; 
                color: #805ad5;
                background-color: #faf5ff;
                border-radius: 10px;
                padding: 10px;
            """)
        else:
            self._state_label.setText(str(state))
            self._state_label.setStyleSheet("""
                font-size: 48px; 
                font-weight: bold; 
                color: #2b6cb0;
                background-color: #ebf8ff;
                border-radius: 10px;
                padding: 15px;
            """)
        
    def _on_sensor_changed(self, value):
        """Update sensor label (called from main thread)"""
        self._sensor_value = value
    
    def _on_pot_changed(self, value):
        """Update potentiometer display (called from main thread)"""
        if value == self._pot_value:
            return
        self._pot_value = value
        self._pot_value_label.setText(str(value))
        self._pot_bar.setValue(value)
    
    def _on_fsr_changed(self, value):
        """Update FSR display (called from main thread)"""
        if value == self._fsr_value:
            return
        self._fsr_value = value
        self._fsr_value_label.setText(str(value))
        self._fsr_bar.setValue(value)
        
        # Change color only when pressure level changes
        if value < 500:
            level = 0
            style = """
                font-size: 18px; 
                font-weight: bold; 
                color: #38a169;
                background-color: #f0fff4;
                border-radius: 6px;
                padding: 5px 10px;
            """
        elif value < 2000:
            level = 1
            style = """
                font-size: 18px; 
                font-weight: bold; 
                color: #d69e2e;
                background-color: #fefcbf;
                border-radius: 6px;
                padding: 5px 10px;
            """
        else:
            level = 2
            style = """
                font-size: 18px; 
                font-weight: bold; 
                color: #e53e3e;
                background-color: #fff5f5;
                border-radius: 6px;
                padding: 5px 10px;
            """
        if level != self._fsr_level:
            self._fsr_level = level
            self._fsr_value_label.setStyleSheet(style)
    
    def _on_encoder_changed(self, value):
        """Update encoder display (called from main thread)"""
        if value == self._encoder_value:
            return
        self._encoder_value = value
        self._encoder_value_label.setText(str(value))
    
    def _on_tof_changed(self, data):
        """Update TOF depth visualization (called from main thread)"""
        if data == self._tof_data:
            return
        self._tof_data = data
        self._tof_widget.setData(data)
            
    def _update_ros(self):
        """Process ROS callbacks"""
        if rclpy.ok():
            rclpy.spin_once(self._node, timeout_sec=0)
            
    def shutdown_plugin(self):
        """Clean up on shutdown"""
        self._update_timer.stop()
        
    def save_settings(self, plugin_settings, instance_settings):
        instance_settings.set_value('servo', self._servo_value)
        instance_settings.set_value('step', self._step_value)
        instance_settings.set_value('dc_vel', self._dc_vel_value)
        instance_settings.set_value('dc_pos', self._dc_pos_value)
        instance_settings.set_value('dc_control_mode', self._dc_control_mode)
        
    def restore_settings(self, plugin_settings, instance_settings):
        servo = instance_settings.value('servo', 0)
        step = instance_settings.value('step', 0)
        dc_vel = instance_settings.value('dc_vel', 0)
        dc_pos = instance_settings.value('dc_pos', 0)
        dc_mode = instance_settings.value('dc_control_mode', 'velocity')
        
        if servo and len(self._sliders) > 0:
            self._sliders[0].setValue(int(servo))
        if step and len(self._sliders) > 1:
            self._sliders[1].setValue(int(step))
        if dc_vel and len(self._sliders) > 2:
            self._sliders[2].setValue(int(dc_vel))
        if dc_pos and len(self._sliders) > 3:
            self._sliders[3].setValue(int(dc_pos))
        if dc_mode == 'position':
            self._dc_position_radio.setChecked(True)