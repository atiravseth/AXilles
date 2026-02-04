/*
 * Arduino Controller for ROS2 Dashboard - Two State System with PID
 * 
 * STATE 1 (controlMode = 1): GUI Dashboard Control
 *   - Servo, DC motor, Stepper controlled via ROS2 GUI
 *   - DC Motor: Velocity mode OR Position mode (PID with encoder)
 * 
 * STATE 2 (controlMode = 2): Sensor-Based Autonomous Control
 *   - DC motor & Servo controlled by potentiometer
 *   - Stepper controlled by FSR (pressure sensor)
 * 
 * Command Format: Single letter followed by a number
 *   V90  - Set servo to 90 degrees
 *   T400 - Set stepper position
 *   P128 - Set DC motor PWM (0-255) in velocity mode
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
 *   F200 - FSR value
 *   E45  - Encoder position
 *   X1   - Current state/mode
 */

#include <ESP32Servo.h>

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
#define FSR_PIN 39

// Button for manual mode switch (optional)
#define BUTTON_PIN 4




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
    
    void evalu(long value, long target, float deltaT, int &pwr, int &dir) {
      // Calculate error
      long e = target - value;
      
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

// ==================== GLOBAL VARIABLES ====================
Servo myServo;

// Control Mode: 1 = GUI Control, 2 = Sensor-Based Autonomous
int controlMode = 1;
bool guiEnabled = false;

// DC Motor Control Mode: "velocity" or "position"
String dcControlMode = "velocity";

// Motor values (for GUI mode)
int servoPosition = 90;
int stepperTarget = 0;
int stepperPosition = 0;
int dcVelocity = 0;
long dcPositionTarget = 0;  // For position control
bool dcDirectionCW = true;

// PID controller for DC motor position control
SimplePID dcPID;

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

// PID timing
long prevT = 0;

// Button debouncing
bool lastButtonState = HIGH;
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
  
  // Set ADC resolution
  analogReadResolution(12);
  
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
  
  // Button
  pinMode(BUTTON_PIN, INPUT_PULLUP);
  
  // Initialize PID
  dcPID.setParams(1.0, 0.05, 0.01, 255);
  
  prevT = micros();
  
  Serial.println("Arduino Controller Ready");
  delay(1);
}

// ==================== MAIN LOOP ====================
void loop() {
  // Process serial commands
  processSerial();
  
  // Check button for manual mode switch
  checkButton();
  
  // Run appropriate control mode
  if (controlMode == 1 && guiEnabled) {
    // GUI Mode
    updateStepper();
    
    // Update DC motor based on control mode
    if (dcControlMode == "position") {
      updateDCMotorPosition();
    }
    // Velocity mode is handled directly in setDCVelocity()
    
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
  
  delay(1);
}

// ==================== AUTONOMOUS MODE ====================
void runAutonomousMode() {
  // DC Motor & Servo controlled by potentiometer
  int motorPWM = map(potValue, 0, 4095, 0, 255);
  analogWrite(DC_PWM_PIN, motorPWM);
  digitalWrite(DC_IN1_PIN, dcDirectionCW ? HIGH : LOW);
  digitalWrite(DC_IN2_PIN, dcDirectionCW ? LOW : HIGH);
  
  // Servo follows potentiometer (10-170 degrees)
  int servoAngle = map(potValue, 0, 4095, 10, 170);
  if (abs(servoAngle - lastServoAngle) > 1) {
    myServo.write(servoAngle);
    lastServoAngle = servoAngle;
  }
  
  // Stepper controlled by FSR
  if (fsrValue > 100) {
    stepInterval = map(fsrValue, 100, 4095, 2000, 500);
    stepInterval = constrain(stepInterval, 500, 2000);
    
    if (millis() - lastStepTime >= stepInterval) {
      digitalWrite(STEP_PIN, HIGH);
      delayMicroseconds(50);
      digitalWrite(STEP_PIN, LOW);
      lastStepTime = millis();
    }
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
  dcPID.evalu(currentPos, dcPositionTarget, deltaT, pwr, dir);
  
  // Apply to motor
  setMotor(dir, pwr, DC_PWM_PIN, DC_IN1_PIN, DC_IN2_PIN);
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
      
    case 'P':  // DC motor PWM (0-255) - velocity mode
    case 'p':
      {
        int value = cmd.substring(1).toInt();
        if (controlMode == 1 && dcControlMode == "velocity") {
          setDCVelocity(value);
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
      dcPID.reset();
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
      dcPID.reset();
      dcPositionTarget = getEncoderPosition();
      prevT = micros();
    } else {
      // Switching to velocity mode - stop motor
      stopDCMotor();
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
  
  if (controlMode != 1 || dcControlMode != "velocity") return;
  
  if (dcVelocity == 0) {
    stopDCMotor();
    return;
  }
  
  digitalWrite(DC_IN1_PIN, dcDirectionCW ? HIGH : LOW);
  digitalWrite(DC_IN2_PIN, dcDirectionCW ? LOW : HIGH);
  analogWrite(DC_PWM_PIN, dcVelocity);
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
  potValue = analogRead(POTENTIOMETER_PIN);
  fsrValue = analogRead(FSR_PIN);
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
  dcPositionTarget = 0;
  dcDirectionCW = true;
  encoderCount = 0;
  controlMode = 1;
  guiEnabled = false;
  dcControlMode = "velocity";
  
  dcPID.reset();
  
  myServo.write(90);
  
  Serial.println("Reset complete");
}
