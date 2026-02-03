#include <Encoder.h>
#include "simplePID.h"
// Encoder pins
Encoder myEncoder(2, 3);
// Motor driver pins (adjust to your setup)
#define MOTOR_PWM 5
#define MOTOR_DIR1 7
#define MOTOR_DIR2 8
// PID gains
float kp = 1.5;
float ki = 0.05;
float kd = 0;
// PID variables
float targetPosition = 0;
float currentPosition = 0;
float error = 0;
float lastError = 0;
float integral = 0;
float output = 0;

bool plotterMode = true; // Set to false for readable serial monitor

void setup() {
  Serial.begin(115200);
  
  pinMode(MOTOR_PWM, OUTPUT);
  pinMode(MOTOR_DIR1, OUTPUT);
  pinMode(MOTOR_DIR2, OUTPUT);
  
  stopMotor();
  myEncoder.write(0);
  
  if (!plotterMode) {
    Serial.println("Simple PID Control Ready");
    Serial.println("Enter target position (e.g., 1000 or -500)");
  }
}

void loop() {
  // Read target position from serial
  if (Serial.available() > 0) {
    String input = Serial.readStringUntil('\n');
    input.trim();
    
    if (input.length() > 0) {
      long newTarget = input.toInt();
      
      // Only update if target actually changed
      if (newTarget != targetPosition) {
        targetPosition = newTarget;
        integral = 0; // Reset integral
      }
    }
  }
  
  // Get current position
  currentPositierroron = myEncoder.read();
  
  // Calculate error
  error = targetPosition - currentPosition;
  
  // PID calculations
  integral += error;
//  integral = constrain(integral, -1000, 1000); // Anti-windup
  
  float derivative = error - lastError;
  
  output = (kp * error) + (ki * integral) + (kd * derivative);
  output = constrain(output, -255, 255);
  
  // Control motor
  if (abs(error) < 5) {
    // Close enough - stop
    stopMotor();
  } else if (output > 0) {
    // Move forward
    digitalWrite(MOTOR_DIR1, HIGH);
    digitalWrite(MOTOR_DIR2, LOW);
    analogWrite(MOTOR_PWM, abs(output));
  } else {
    // Move backward
    digitalWrite(MOTOR_DIR1, LOW);
    digitalWrite(MOTOR_DIR2, HIGH);
    analogWrite(MOTOR_PWM, abs(output));
  }
  
  lastError = error;
  
  // Print for Serial Plotter (space-separated values on one line)
  if (plotterMode) {
    Serial.print("Target:");
    Serial.print(targetPosition);
    Serial.print(" Current:");
    Serial.print(currentPosition);
    Serial.print(" Error:");
    Serial.println(error);
  } else {
    Serial.print("Target: ");
    Serial.print(targetPosition);
    Serial.print(" | Current: ");
    Serial.print(currentPosition);
    Serial.print(" | Error: ");
    Serial.println(error);
  }
  
  delay(10);
}

void stopMotor() {
  digitalWrite(MOTOR_DIR1, LOW);
  digitalWrite(MOTOR_DIR2, LOW);
  analogWrite(MOTOR_PWM, 0);
}
