/*
 * Arduino Controller for ROS2 Dashboard - Two State System
 * 
 * STATE 1 (controlMode = 1): GUI Dashboard Control
 *   - Servo, DC motor, Stepper controlled via ROS2 GUI
 * 
 * STATE 2 (controlMode = 2): Sensor-Based Autonomous Control
 *   - DC motor & Servo controlled by potentiometer
 *   - Stepper controlled by FSR (pressure sensor)
 * 
 * Command Format: Single letter followed by a number
 *   V90  - Set servo to 90 degrees
 *   T400 - Set stepper position
 *   P128 - Set DC motor PWM (0-255)
 *   D1   - Set DC motor direction CCW / D0 for CW
 *   G1   - GUI control enabled / G0 disabled
 *   C1   - Set control mode to 1 (GUI) / C2 for sensor mode
 *   R0   - Reset all
 * 
 * Sensor Output Format:
 *   P512 - Potentiometer value
 *   F200 - FSR value
 *   E45  - Encoder position
 *   X1   - Current state/mode
 */

#include <Servo.h>

// ==================== PIN DEFINITIONS ====================
// Servo Motor
#define SERVO_PIN 6

// Stepper Motor
#define STEP_PIN 9
#define DIR_PIN 10
#define ENABLE_PIN 11  // LOW = enabled (for A4988/DRV8825)

// DC Motor (L298N)
#define DC_PWM_PIN 5   // ENA
#define DC_IN1_PIN 7   // IN1
#define DC_IN2_PIN 8   // IN2

// DC Motor Encoder
#define ENCODER_A_PIN 2  // chA (interrupt)
#define ENCODER_B_PIN 3  // chB

// Sensors
#define POTENTIOMETER_PIN A0
#define FSR_PIN A1

// Button for manual mode switch (optional)
#define BUTTON_PIN 4

// ==================== GLOBAL VARIABLES ====================
Servo myServo;

// Control Mode: 1 = GUI Control, 2 = Sensor-Based Autonomous
int controlMode = 1;
bool guiEnabled = false;

// Motor values (for GUI mode)
int servoPosition = 90;
int stepperTarget = 0;
int stepperPosition = 0;
int dcVelocity = 0;
bool dcDirectionCW = true;

// Encoder
volatile long encoderCount = 0;

// Sensor values
int potValue = 0;
int fsrValue = 0;

// Timing
unsigned long lastSensorRead = 0;
const unsigned long SENSOR_INTERVAL = 50;  // 50ms = 20Hz

unsigned long lastStepTime = 0;
int stepInterval = 1000;

// Button debouncing
bool lastButtonState = HIGH;
unsigned long lastDebounceTime = 0;
const unsigned long debounceDelay = 50;

// Serial
String inputBuffer = "";

// Last servo angle for smoothing
int lastServoAngle = 90;

// ==================== SETUP ====================
void setup() {
  Serial.begin(115200);
  
  // Servo
  myServo.attach(SERVO_PIN);
  myServo.write(90);
  
  // Stepper
  pinMode(STEP_PIN, OUTPUT);
  pinMode(DIR_PIN, OUTPUT);
  pinMode(ENABLE_PIN, OUTPUT);
  digitalWrite(ENABLE_PIN, LOW);  // Enable stepper driver
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
  
  // Button
  pinMode(BUTTON_PIN, INPUT_PULLUP);
  
  Serial.println("Arduino Controller Ready");
}

// ==================== MAIN LOOP ====================
void loop() {
  // Process serial commands
  processSerial();
  
  // Check button for manual mode switch
  checkButton();
  
  // Run appropriate control mode
  if (controlMode == 1 && guiEnabled) {
    // GUI Mode - stepper updates handled by updateStepper()
    updateStepper();
  } else if (controlMode == 2) {
    // Sensor-Based Autonomous Mode
    runAutonomousMode();
  }
  
  // Read and send sensor data periodically
  if (millis() - lastSensorRead >= SENSOR_INTERVAL) {
    lastSensorRead = millis();
    readSensors();
    
    if (guiEnabled || controlMode == 2) {
      sendSensorData();
    }
    sendState();
  }
}

// ==================== AUTONOMOUS MODE ====================
void runAutonomousMode() {
  // DC Motor & Servo controlled by potentiometer
  int motorPWM = map(potValue, 0, 1023, 0, 255);
  analogWrite(DC_PWM_PIN, motorPWM);
  digitalWrite(DC_IN1_PIN, dcDirectionCW);
  digitalWrite(DC_IN2_PIN, !dcDirectionCW);
  
  // Servo follows potentiometer (10-170 degrees)
  int servoAngle = map(potValue, 0, 1023, 10, 170);
  if (abs(servoAngle - lastServoAngle) > 1) {
    myServo.write(servoAngle);
    lastServoAngle = servoAngle;
  }
  
  // Stepper controlled by FSR
  if (fsrValue > 10) {
    stepInterval = map(fsrValue, 10, 800, 50, 5);
    stepInterval = constrain(stepInterval, 5, 100);
    
    if (millis() - lastStepTime >= stepInterval) {
      digitalWrite(STEP_PIN, HIGH);
      delayMicroseconds(50);
      digitalWrite(STEP_PIN, LOW);
      lastStepTime = millis();
    }
  }
}

// ==================== BUTTON CHECK ====================
void checkButton() {
  bool reading = digitalRead(BUTTON_PIN);
  
  if (reading != lastButtonState) {
    lastDebounceTime = millis();
  }
  
  if ((millis() - lastDebounceTime) > debounceDelay) {
    if (reading == LOW && lastButtonState == HIGH) {
      // Toggle direction in autonomous mode
      if (controlMode == 2) {
        dcDirectionCW = !dcDirectionCW;
        digitalWrite(DIR_PIN, dcDirectionCW ? HIGH : LOW);
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
  int value = cmd.substring(1).toInt();
  
  switch (cmdType) {
    case 'C':  // Control mode (1=GUI, 2=Sensor)
    case 'c':
      setControlMode(value);
      break;
      
    case 'V':  // Servo (0-180)
    case 'v':
      if (controlMode == 1) setServo(value);
      break;
      
    case 'T':  // Stepper target
    case 't':
      if (controlMode == 1) setStepperTarget(value);
      break;
      
    case 'P':  // DC motor PWM (0-255)
    case 'p':
      if (controlMode == 1) setDCVelocity(value);
      break;
      
    case 'D':  // DC direction (0=CW, 1=CCW)
    case 'd':
      setDCDirection(value == 1);
      break;
      
    case 'G':  // GUI enable
    case 'g':
      guiEnabled = (value == 1);
      if (!guiEnabled && controlMode == 1) {
        stopAllMotors();
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
    } else {
      // Switching to autonomous mode
      // Motors will be controlled by sensors
    }
  }
}

// ==================== SERVO CONTROL ====================
void setServo(int angle) {
  servoPosition = constrain(angle, 0, 180);
  myServo.write(servoPosition);
  lastServoAngle = servoPosition;
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
  dcVelocity = constrain(velocity, 0, 255);
  updateDCMotor();
}

void setDCDirection(bool ccw) {
  dcDirectionCW = !ccw;
  updateDCMotor();
}

void updateDCMotor() {
  if (controlMode != 1) return;  // Only update in GUI mode
  
  if (dcVelocity == 0) {
    stopDCMotor();
    return;
  }
  
  digitalWrite(DC_IN1_PIN, dcDirectionCW);
  digitalWrite(DC_IN2_PIN, !dcDirectionCW);
  analogWrite(DC_PWM_PIN, dcVelocity);
}

void stopDCMotor() {
  digitalWrite(DC_IN1_PIN, LOW);
  digitalWrite(DC_IN2_PIN, LOW);
  analogWrite(DC_PWM_PIN, 0);
}

// ==================== SENSOR READING ====================
void readSensors() {
  potValue = analogRead(POTENTIOMETER_PIN);
  fsrValue = analogRead(FSR_PIN);
}

// ==================== ENCODER ====================
void encoderISR() {
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

// ==================== SEND DATA ====================
void sendSensorData() {
  Serial.print("P");
  Serial.println(potValue);
  
  Serial.print("F");
  Serial.println(fsrValue);
  
  Serial.print("E");
  Serial.println(getEncoderPosition());
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
  dcDirectionCW = true;
  encoderCount = 0;
  controlMode = 1;
  guiEnabled = false;
  
  myServo.write(90);
  
  Serial.println("Reset complete");
}
