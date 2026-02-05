/*
 * ESP32 DevkitV1 Controller for ROS2 Dashboard - Two State System with PID
 * 
 * STATE 1 (controlMode = 1): GUI Dashboard Control
 *   - Servo, DC motor, Stepper controlled via ROS2 GUI
 *   - DC Motor: Velocity mode (closed-loop °/s) OR Position mode (PID with encoder)
 * 
 * STATE 2 (controlMode = 2): Sensor-Based Autonomous Control
 *   - DC motor & Servo controlled by potentiometer
 *   - Stepper controlled by FSR (pressure sensor)
 *   - Button on D4 toggles DC motor direction with debounce
 * 
 * Command Format: Single letter followed by a number
 *   V90  - Set servo to 90 degrees
 *   T400 - Set stepper position
 *   W100 - Set DC motor velocity in °/s (velocity mode, closed-loop)
 *   M500 - Set DC motor target position in position mode
 *   D1   - Set DC motor direction CCW / D0 for CW
 *   G1   - GUI control enabled / G0 disabled
 *   C1   - Set control mode to 1 (GUI) / C2 for sensor mode
 *   Qvelocity   - Set DC control to velocity mode
 *   Qposition   - Set DC control to position mode
 *   R0   - Reset all
 * 
 * Sensor Output Format:
 *   P512 - Potentiometer value
 *   F200 - FSR value (moving average filtered)
 *   E45  - Encoder position
 *   S123 - Encoder velocity (°/s)
 *   X1   - Current state/mode
 *   Z... - TOF 8x8 depth data
 */

#include <ESP32Servo.h>
#include <Wire.h>
#include <SparkFun_VL53L5CX_Library.h>

// ==================== PIN DEFINITIONS ====================
// Servo Motor
#define SERVO_PIN 18

// Stepper Motor
#define STEP_PIN 13
#define DIR_PIN 14

// DC Motor (L298N)
#define DC_PWM_PIN 25   // ENA
#define DC_IN1_PIN 26   // IN1
#define DC_IN2_PIN 27   // IN2

// DC Motor Encoder
#define ENCODER_A_PIN 32  // chA (interrupt)
#define ENCODER_B_PIN 33  // chB

// Sensors
#define POTENTIOMETER_PIN 36
#define FSR_PIN 12

// Button for direction control in autonomous mode
#define BUTTON_PIN 4

// TOF Sensor I2C
#define TOF_SDA_PIN 21
#define TOF_SCL_PIN 22

// Encoder counts per revolution (adjust based on your encoder)
#define ENCODER_CPR 20  // Counts per revolution (adjust as needed)




// ==================== PID CLASS ====================
class SimplePID {
  private:
    float kp, kd, ki, umax;
    float eprev, eintegral;
    
  public:
    SimplePID() : kp(1.0), kd(0.05), ki(0.01), umax(255), eprev(0.0), eintegral(0.0) {}
    
    void setParams(float kpIn, float kdIn, float kiIn, float umaxIn) {
      kp = kpIn; 
      kd = kdIn; 
      ki = kiIn; 
      umax = umaxIn;
    }
    
    void evalu(float value, float target, float deltaT, int &pwr, int &dir) {
      // Calculate error
      float e = target - value;
      
      // Derivative
      float dedt = (e - eprev) / deltaT;
      
      // Integral with anti-windup
      eintegral = eintegral + e * deltaT;
      float integralMax = 1000.0; // Prevent integral windup
      if (eintegral > integralMax) eintegral = integralMax;
      if (eintegral < -integralMax) eintegral = -integralMax;
      
      // PID calculation
      float u = kp * e + kd * dedt + ki * eintegral;
      
      // Power and direction
      pwr = (int) fabs(u);
      if (pwr > umax) {
        pwr = umax;
      }
      
      dir = (u >= 0) ? 1 : -1;
      
      eprev = e;
    }
    
    void reset() {
      eprev = 0.0;
      eintegral = 0.0;
    }
};

// ==================== FSR MOVING AVERAGE FILTER ====================
class MovingAverageFilter {
  private:
    int buffer[3];
    int index;
    bool filled;
    
  public:
    MovingAverageFilter() : index(0), filled(false) {
      for (int i = 0; i < 3; i++) {
        buffer[i] = 0;
      }
    }
    
    int filter(int newValue) {
      buffer[index] = newValue;
      index = (index + 1) % 3;
      if (index == 0) filled = true;
      
      int sum = 0;
      int count = filled ? 3 : index;
      if (count == 0) return newValue;
      
      for (int i = 0; i < count; i++) {
        sum += buffer[i];
      }
      return sum / count;
    }
    
    void reset() {
      index = 0;
      filled = false;
      for (int i = 0; i < 3; i++) {
        buffer[i] = 0;
      }
    }
};

// ==================== GLOBAL VARIABLES ====================
Servo myServo;

// TOF Sensor
SparkFun_VL53L5CX tofSensor;
VL53L5CX_ResultsData tofData;
bool tofAvailable = false;
unsigned long lastTofRead = 0;
const unsigned long TOF_INTERVAL = 100;  // 100ms = 10Hz for TOF

// Control Mode: 1 = GUI Control, 2 = Sensor-Based Autonomous
int controlMode = 1;
bool guiEnabled = false;

// DC Motor Control Mode: "velocity" or "position"
String dcControlMode = "velocity";

// Motor values (for GUI mode)
int servoPosition = 90;
int stepperTarget = 0;
int stepperPosition = 0;
unsigned long lastStepperStepMicros = 0;
const unsigned long STEPPER_STEP_INTERVAL_US = 800;  // Step interval for smooth motion
const unsigned long STEPPER_PULSE_WIDTH_US = 5;      // Step pulse width
int dcVelocity = 0;
float dcVelocityTarget = 0.0;  // Target velocity in °/s for closed-loop control
float dcVelocityRamped = 0.0;  // Ramped velocity target for smooth acceleration
const float VELOCITY_RAMP_RATE = 360.0;  // Max °/s change per second (1 rev/s per second)
long dcPositionTarget = 0;  // For position control
bool dcDirectionCW = true;

// PID controller for DC motor position control
SimplePID dcPosPID;
// PID controller for DC motor velocity control (closed-loop)
SimplePID dcVelPID;

// Moving average filter for FSR
MovingAverageFilter fsrFilter;

// Encoder
volatile long encoderCount = 0;
long lastEncoderCount = 0;
unsigned long lastVelocityCalcTime = 0;
float currentVelocity = 0.0;  // Current velocity in °/s

// Sensor values
int potValue = 0;
int fsrValue = 0;
int fsrRaw = 0;  // Raw FSR value before filtering

// Timing
unsigned long lastSensorRead = 0;
const unsigned long SENSOR_INTERVAL = 50;  // 50ms = 20Hz
const unsigned long SENSOR_INTERVAL_MOVING = 200;  // Reduce serial load while stepper moves

unsigned long lastStepTime = 0;
int stepInterval = 1000;

// PID timing
long prevT = 0;

// Button debouncing for D4 (autonomous mode direction control)
bool lastButtonState = HIGH;
bool buttonState = HIGH;
unsigned long lastDebounceTime = 0;
const unsigned long debounceDelay = 50;

// Serial
String inputBuffer = "";

// Last servo angle for smoothing
int lastServoAngle = 90;



// ==================== ENCODER ====================
void IRAM_ATTR encoderISR() {
  if (digitalRead(ENCODER_B_PIN) == HIGH) {
    encoderCount++;
  } else {
    encoderCount--;
  }
}

long getEncoderPosition() {
  noInterrupts();
  long pos = encoderCount;
  interrupts();
  return pos;
}



// ==================== SETUP ====================
void setup() {
  Serial.begin(115200);
  delay(100);  // Give serial time to initialize
  Serial.println("ESP32 Controller Starting...");
  
  // Set ADC resolution
  analogReadResolution(12);
  analogSetAttenuation(ADC_11db);
  
  // Initialize I2C for TOF sensor with error handling
  Wire.begin(TOF_SDA_PIN, TOF_SCL_PIN);
  Wire.setClock(100000);  // 100kHz for stability
  
  // Initialize TOF sensor with timeout protection
  tofAvailable = false;
  Serial.println("Checking TOF sensor...");
  
  // Only try to initialize if I2C device responds
  Wire.beginTransmission(0x29);  // VL53L5CX default address
  int i2cResult = Wire.endTransmission();
  
  if (i2cResult == 0) {
    Serial.println("TOF I2C device found, initializing...");
    if (tofSensor.begin() == true) {
      tofSensor.setResolution(8*8);  // 8x8 resolution
      tofSensor.startRanging();
      tofAvailable = true;
      Serial.println("TOF sensor initialized successfully");
    } else {
      Serial.println("TOF sensor init failed");
    }
  } else {
    Serial.println("TOF sensor not connected (I2C scan failed)");
  }
  
  // Servo
  myServo.attach(SERVO_PIN);
  myServo.write(90);
  
  // Stepper
  pinMode(STEP_PIN, OUTPUT);
  pinMode(DIR_PIN, OUTPUT);
  digitalWrite(DIR_PIN, HIGH);
  
  // DC Motor
  pinMode(DC_PWM_PIN, OUTPUT);
  pinMode(DC_IN1_PIN, OUTPUT);
  pinMode(DC_IN2_PIN, OUTPUT);
  stopDCMotor();
  
  // Encoder
  pinMode(ENCODER_A_PIN, INPUT_PULLUP);
  pinMode(ENCODER_B_PIN, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(ENCODER_A_PIN), encoderISR, RISING);
  
  // Button (D4) for direction control in autonomous mode
  pinMode(BUTTON_PIN, INPUT_PULLUP);
  
  // Initialize PIDs
  dcPosPID.setParams(1.0, 0.05, 0.01, 255);
  // Smoother velocity PID: lower kp, lower ki, add kd for damping
  dcVelPID.setParams(0.8, 0.2, 0.1, 255);  // PID for velocity control - tuned for smooth operation
  
  prevT = micros();
  lastVelocityCalcTime = micros();
  
  Serial.println("ESP32 Controller Ready");
  delay(1);
}

// ==================== MAIN LOOP ====================
void loop() {
  // Process serial commands
  processSerial();
  
  // Calculate current velocity from encoder
  updateVelocity();
  
  // Run appropriate control mode
  if (controlMode == 1 && guiEnabled) {
    // GUI Mode
    updateStepper();
    
    // Update DC motor based on control mode
    if (dcControlMode == "position") {
      updateDCMotorPosition();
    }
    // Note: velocity mode uses direct PWM control via W command
    // No need to call updateDCMotorVelocity() - motor is controlled directly
    
  } else if (controlMode == 2) {
    // Sensor-Based Autonomous Mode
    // Check button for direction toggle (D4)
    checkButton();
    runAutonomousMode();
  }
  
  // Read and send sensor data periodically
  unsigned long sensorInterval = (controlMode == 1 && stepperPosition != stepperTarget)
                                   ? SENSOR_INTERVAL_MOVING
                                   : SENSOR_INTERVAL;
  if (millis() - lastSensorRead >= sensorInterval) {
    lastSensorRead = millis();
    readSensors();
    
    if (guiEnabled || controlMode == 2) {
      sendSensorData();
    }
    sendState();
  }
  
  // Read and send TOF data (less frequently)
  if (tofAvailable && guiEnabled && (millis() - lastTofRead >= TOF_INTERVAL)) {
    lastTofRead = millis();
    readAndSendTofData();
  }
  
  delay(1);
}

// ==================== AUTONOMOUS MODE ====================
void runAutonomousMode() {
  // Ensure sensors are read fresh for autonomous control
  potValue = analogRead(POTENTIOMETER_PIN);
  fsrRaw = analogRead(FSR_PIN);
  fsrValue = fsrFilter.filter(fsrRaw);
  
  // DC Motor & Servo controlled by potentiometer
  int motorPWM = map(potValue, 0, 4095, 0, 255);
  analogWrite(DC_PWM_PIN, motorPWM);
  digitalWrite(DC_IN1_PIN, dcDirectionCW ? HIGH : LOW);
  digitalWrite(DC_IN2_PIN, dcDirectionCW ? LOW : HIGH);
  
  // Servo follows potentiometer (10-170 degrees) - NO smoothing for instant response
  int servoAngle = map(potValue, 0, 4095, 10, 170);
  myServo.write(servoAngle);
  lastServoAngle = servoAngle;
  
  // Stepper controlled by FSR (filtered value)
  // BUG FIX: Use fsrValue (filtered) which is now properly updated
  if (fsrValue > 100) {
    // Higher FSR value = faster stepping
    stepInterval = map(fsrValue, 100, 4095, 100, 10);
    // stepInterval = constrain(stepInterval, 200, 2000);
    
    // Direction based on FSR threshold
    if (fsrValue > 2000) {
      digitalWrite(DIR_PIN, HIGH);  // CW for high pressure
    } else {
      digitalWrite(DIR_PIN, LOW);   // CCW for low pressure
    }
    
    if (millis() - lastStepTime >= (unsigned long)stepInterval) {
      digitalWrite(STEP_PIN, HIGH);
      delayMicroseconds(100);
      digitalWrite(STEP_PIN, LOW);
      lastStepTime = millis();
    }
  }
}

// ==================== VELOCITY CALCULATION ====================
void updateVelocity() {
  unsigned long currentTime = micros();
  float deltaT = (currentTime - lastVelocityCalcTime) / 1000000.0;
  
  if (deltaT >= 0.01) {  // Calculate every 10ms minimum
    long currentCount = getEncoderPosition();
    long deltaCount = currentCount - lastEncoderCount;
    
    // Calculate velocity in degrees per second
    // velocity = (counts / deltaT) * (360 / ENCODER_CPR)
    currentVelocity = (deltaCount / deltaT) * (360.0 / ENCODER_CPR);
    
    lastEncoderCount = currentCount;
    lastVelocityCalcTime = currentTime;
  }
}

// ==================== DC MOTOR PID POSITION CONTROL ====================
void updateDCMotorPosition() {
  // Calculate deltaT
  long currT = micros();
  float deltaT = ((float)(currT - prevT)) / 1.0e6;
  prevT = currT;
  
  // Prevent division by zero
  if (deltaT < 0.001) deltaT = 0.001;
  
  // Read encoder position
  long currentPos = getEncoderPosition();
  
  // Run PID
  int pwr, dir;
  dcPosPID.evalu((float)currentPos, (float)dcPositionTarget, deltaT, pwr, dir);
  
  // Apply to motor
  setMotor(dir, pwr, DC_PWM_PIN, DC_IN1_PIN, DC_IN2_PIN);
}

// ==================== DC MOTOR PID VELOCITY CONTROL (CLOSED-LOOP) ====================
void updateDCMotorVelocity() {
  // Calculate deltaT
  long currT = micros();
  float deltaT = ((float)(currT - prevT)) / 1.0e6;
  prevT = currT;
  
  // Prevent division by zero
  if (deltaT < 0.001) deltaT = 0.001;
  
  // If target velocity is near zero, stop the motor smoothly
  if (fabs(dcVelocityTarget) < 5.0) {
    // Ramp down smoothly
    if (fabs(dcVelocityRamped) > 5.0) {
      float rampChange = VELOCITY_RAMP_RATE * deltaT;
      if (dcVelocityRamped > 0) {
        dcVelocityRamped -= rampChange;
        if (dcVelocityRamped < 0) dcVelocityRamped = 0;
      } else {
        dcVelocityRamped += rampChange;
        if (dcVelocityRamped > 0) dcVelocityRamped = 0;
      }
    } else {
      stopDCMotor();
      dcVelPID.reset();
      dcVelocityRamped = 0;
      return;
    }
  } else {
    // Ramp velocity towards target for smooth acceleration
    float targetWithSign = dcDirectionCW ? dcVelocityTarget : -dcVelocityTarget;
    float rampChange = VELOCITY_RAMP_RATE * deltaT;
    
    if (dcVelocityRamped < targetWithSign) {
      dcVelocityRamped += rampChange;
      if (dcVelocityRamped > targetWithSign) dcVelocityRamped = targetWithSign;
    } else if (dcVelocityRamped > targetWithSign) {
      dcVelocityRamped -= rampChange;
      if (dcVelocityRamped < targetWithSign) dcVelocityRamped = targetWithSign;
    }
  }
  
  // Run PID on velocity (current velocity vs ramped target)
  int pwr, dir;
  dcVelPID.evalu(currentVelocity, dcVelocityRamped, deltaT, pwr, dir);
  
  // Apply to motor
  setMotor(dir, pwr, DC_PWM_PIN, DC_IN1_PIN, DC_IN2_PIN);
}

// ==================== BUTTON CHECK (Autonomous Mode Direction Control) ====================
void checkButton() {
  // Only active in autonomous mode (controlMode == 2)
  if (controlMode != 2) return;
  
  bool reading = digitalRead(BUTTON_PIN);
  
  // Reset debounce timer if the button state changed
  if (reading != lastButtonState) {
    lastDebounceTime = millis();
  }
  
  // If debounce time has passed, update button state
  if ((millis() - lastDebounceTime) > debounceDelay) {
    // If button state has changed
    if (reading != buttonState) {
      buttonState = reading;
      
      // Only toggle direction on button press (LOW because of INPUT_PULLUP)
      if (buttonState == LOW) {
        dcDirectionCW = !dcDirectionCW;
        Serial.print("Direction: ");
        Serial.println(dcDirectionCW ? "CW" : "CCW");
      }
    }
  }
  
  lastButtonState = reading;
}

// ==================== SERIAL PROCESSING ====================
void processSerial() {
  while (Serial.available() > 0) {
    char c = Serial.read();
    
    if (c == '\n' || c == '\r') {
      if (inputBuffer.length() > 0) {
        parseCommand(inputBuffer);
        inputBuffer = "";
      }
    } else {
      inputBuffer += c;
    }
  }
}

void parseCommand(String cmd) {
  if (cmd.length() < 2) return;
  
  char cmdType = cmd.charAt(0);
  
  switch (cmdType) {
    case 'C':  // Control mode (1=GUI, 2=Sensor)
    case 'c':
      {
        int value = cmd.substring(1).toInt();
        setControlMode(value);
      }
      break;
      
    case 'V':  // Servo (0-180)
    case 'v':
      {
        int value = cmd.substring(1).toInt();
        if (controlMode == 1) setServo(value);
      }
      break;
      
    case 'T':  // Stepper target
    case 't':
      {
        int value = cmd.substring(1).toInt();
        if (controlMode == 1) setStepperTarget(value);
      }
      break;
      
    case 'P':  // DC motor PWM (0-255) - legacy, now uses W for velocity
    case 'p':
      {
        int value = cmd.substring(1).toInt();
        if (controlMode == 1 && dcControlMode == "velocity") {
          // Legacy PWM mode - convert to approximate velocity
          setDCVelocityTarget((float)map(value, 0, 255, 0, 720));
        }
      }
      break;
    
    case 'W':  // DC motor PWM (0-255) - direct PWM control
    case 'w':
      {
        int value = cmd.substring(1).toInt();
        if (controlMode == 1 && dcControlMode == "velocity") {
          // Direct PWM control - value is already 0-255
          int pwmVal = constrain(value, 0, 255);
          dcVelocity = pwmVal;
          if (pwmVal > 0) {
            setMotor(dcDirectionCW ? 1 : -1, pwmVal, DC_PWM_PIN, DC_IN1_PIN, DC_IN2_PIN);
          } else {
            stopDCMotor();
          }
        }
      }
      break;
    
    case 'M':  // DC motor position target - position mode
    case 'm':
      {
        long value = cmd.substring(1).toInt();
        if (controlMode == 1 && dcControlMode == "position") {
          setDCPositionTarget(value);
        }
      }
      break;
      
    case 'D':  // DC direction (0=CW, 1=CCW)
    case 'd':
      {
        int value = cmd.substring(1).toInt();
        setDCDirection(value == 1);
      }
      break;
      
    case 'Q':  // DC control mode (Qvelocity or Qposition)
    case 'q':
      {
        String mode = cmd.substring(1);
        mode.toLowerCase();
        setDCControlMode(mode);
      }
      break;
      
    case 'G':  // GUI enable
    case 'g':
      {
        int value = cmd.substring(1).toInt();
        guiEnabled = (value == 1);
        if (!guiEnabled && controlMode == 1) {
          stopAllMotors();
        }
      }
      break;
      
    case 'R':  // Reset
    case 'r':
      resetAll();
      break;
      
    default:
      break;
  }
}

// ==================== CONTROL MODE ====================
void setControlMode(int mode) {
  if (mode == 1 || mode == 2) {
    controlMode = mode;
    
    if (mode == 1) {
      // Switching to GUI mode - stop autonomous control
      stopDCMotor();
      dcVelPID.reset();
    } else {
      // Switching to autonomous mode
      // Motors will be controlled by sensors
    }
  }
}

void setDCControlMode(String mode) {
  if (mode == "velocity" || mode == "position") {
    dcControlMode = mode;
    
    if (mode == "position") {
      // Switching to position mode - reset PID and set target to current position
      dcPosPID.reset();
      dcPositionTarget = getEncoderPosition();
      prevT = micros();
    } else {
      // Switching to velocity mode - reset velocity PID and stop motor
      dcVelPID.reset();
      stopDCMotor();
      dcVelocityTarget = 0.0;
      dcVelocityRamped = 0.0;  // Reset ramped velocity
    }
  }
}

// ==================== SERVO CONTROL ====================
void setServo(int angle) {
  servoPosition = constrain(angle, 0, 180);
  myServo.write(servoPosition);  // Direct write for instant response
}

// ==================== STEPPER CONTROL ====================
void setStepperTarget(int target) {
  stepperTarget = target;
}

void updateStepper() {
  if (stepperPosition == stepperTarget) return;
  
  if (stepperTarget > stepperPosition) {
    digitalWrite(DIR_PIN, HIGH);
    stepperPosition++;
  } else {
    digitalWrite(DIR_PIN, LOW);
    stepperPosition--;
  }
  
  digitalWrite(STEP_PIN, HIGH);
  delayMicroseconds(500);
  digitalWrite(STEP_PIN, LOW);
  delayMicroseconds(500);
}

// ==================== DC MOTOR CONTROL ====================
void setDCVelocity(int velocity) {
  // Legacy function - converts PWM value to velocity target
  dcVelocity = constrain(velocity, 0, 255);
  dcVelocityTarget = map(dcVelocity, 0, 255, 0, 720);  // Map to °/s
}

void setDCVelocityTarget(float velocityDegPerSec) {
  // Set target velocity in degrees per second for closed-loop control
  dcVelocityTarget = constrain(velocityDegPerSec, 0.0, 1440.0);  // Max 4 rev/s
  
  if (controlMode != 1 || dcControlMode != "velocity") return;
  
  if (dcVelocityTarget < 5.0) {
    stopDCMotor();
    dcVelPID.reset();
  }
  // Actual motor control is done in updateDCMotorVelocity()
}

void setDCPositionTarget(long target) {
  dcPositionTarget = target;
}

void setDCDirection(bool ccw) {
  dcDirectionCW = !ccw;
  
  // Update motor if in velocity mode
  if (controlMode == 1 && dcControlMode == "velocity" && dcVelocity > 0) {
    digitalWrite(DC_IN1_PIN, dcDirectionCW ? HIGH : LOW);
    digitalWrite(DC_IN2_PIN, dcDirectionCW ? LOW : HIGH);
  }
}

void setMotor(int dir, int pwmVal, int pwmPin, int in1Pin, int in2Pin) {
  analogWrite(pwmPin, pwmVal);
  
  if (dir == 1) {
    digitalWrite(in1Pin, HIGH);
    digitalWrite(in2Pin, LOW);
  }
  else if (dir == -1) {
    digitalWrite(in1Pin, LOW);
    digitalWrite(in2Pin, HIGH);
  }
  else {
    digitalWrite(in1Pin, LOW);
    digitalWrite(in2Pin, LOW);
  }
}

void stopDCMotor() {
  digitalWrite(DC_IN1_PIN, LOW);
  digitalWrite(DC_IN2_PIN, LOW);
  analogWrite(DC_PWM_PIN, 0);
  dcVelocity = 0;
}

// ==================== SENSOR READING ====================
void readSensors() {

  int potValue_el = 0; 
  int fsrValue_el = 0;

  potValue_el = analogRead(POTENTIOMETER_PIN);
  potValue = potValue_el * 9380 / 4095 + 2;
  fsrRaw = analogRead(FSR_PIN);
  // Apply moving average filter to FSR
  fsrValue_el = fsrFilter.filter(fsrRaw);
  fsrValue = fsrValue_el * 100/4095 * 9.81;  
}

// ==================== TOF SENSOR ====================
void readAndSendTofData() {
  if (!tofAvailable) return;
  
  if (tofSensor.isDataReady()) {
    if (tofSensor.getRangingData(&tofData)) {
      // Send TOF data as comma-separated 8x8 values
      // Format: Z<d1>,<d2>,...,<d64>
      Serial.print("Z");
      for (int i = 0; i < 64; i++) {
        Serial.print(tofData.distance_mm[i]);
        if (i < 63) Serial.print(",");
      }
      Serial.println();
    }
  }
}

// ==================== SEND DATA ====================
void sendSensorData() {
  Serial.print("P");
  Serial.println(potValue);
  
  Serial.print("F");
  Serial.println(fsrValue);
  
  Serial.print("E");
  Serial.println(getEncoderPosition());
  
  // Send current velocity in °/s
  Serial.print("S");
  Serial.println((int)currentVelocity);
}

void sendState() {
  Serial.print("X");
  Serial.println(controlMode);
}

// ==================== UTILITY ====================
void stopAllMotors() {
  setServo(90);
  stopDCMotor();
  stepperTarget = stepperPosition;
}

void resetAll() {
  stopAllMotors();
  
  servoPosition = 90;
  stepperPosition = 0;
  stepperTarget = 0;
  dcVelocity = 0;
  dcVelocityTarget = 0.0;
  dcVelocityRamped = 0.0;  // Reset ramped velocity
  dcPositionTarget = 0;
  dcDirectionCW = true;
  encoderCount = 0;
  lastEncoderCount = 0;
  currentVelocity = 0.0;
  controlMode = 1;
  guiEnabled = false;
  dcControlMode = "velocity";
  
  dcPosPID.reset();
  dcVelPID.reset();
  fsrFilter.reset();
  
  myServo.write(90);
  
  Serial.println("Reset complete");
}
