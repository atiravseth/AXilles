#include <Servo.h>

// Pins
#define POT_PIN A0
#define FSR_PIN A1
#define BUTTON_PIN 4

#define ENA 5
#define IN1 7
#define IN2 8

#define STEP_PIN 9
#define DIR_PIN 10
#define ENABLE_PIN 11  // LOW = enabled

#define SERVO_PIN 6

Servo myServo;

int lastServoAngle = 90;
bool motorDir = true;            
bool lastButtonState = HIGH;     
unsigned long lastDebounceTime = 0;
const unsigned long debounceDelay = 50;

unsigned long lastStepTime = 0;  
int stepInterval = 1000;         

void setup() {
  Serial.begin(9600);

  pinMode(ENA, OUTPUT);
  pinMode(IN1, OUTPUT);
  pinMode(IN2, OUTPUT);

  pinMode(STEP_PIN, OUTPUT);
  pinMode(DIR_PIN, OUTPUT);
  pinMode(ENABLE_PIN, OUTPUT);
  digitalWrite(DIR_PIN, HIGH);
  digitalWrite(ENABLE_PIN, LOW);

  pinMode(BUTTON_PIN, INPUT_PULLUP);

  myServo.attach(SERVO_PIN);

  Serial.println("System Ready");
}

void loop() {
  // BUTTON DEBOUNCE 
  bool reading = digitalRead(BUTTON_PIN);
  if (reading != lastButtonState) {
    lastDebounceTime = millis();
  }

  if ((millis() - lastDebounceTime) > debounceDelay) {
    if (reading == LOW && lastButtonState == HIGH) {
      motorDir = !motorDir;
      Serial.println("DC Motor Direction Toggled!"); // debug 
    }
  }
  lastButtonState = reading;

  //POTENTIOMETER
  int potVal = analogRead(POT_PIN);

  // DC motor PWM 
  int motorPWM = map(potVal, 0, 1023, 100, 250);
  analogWrite(ENA, motorPWM);
  digitalWrite(IN1, motorDir);
  digitalWrite(IN2, !motorDir);

  // Servo angle 10-170
  int servoAngle = map(potVal, 0, 1023, 10, 170);


  if (abs(servoAngle - lastServoAngle) > 1) {
    myServo.write(servoAngle);
    lastServoAngle = servoAngle;
  }

  // FSR & Stepper
  int fsrVal = analogRead(FSR_PIN);
  stepInterval = map(fsrVal, 200, 800, 50, 10);

  if (fsrVal > 10) {  
    if (millis() - lastStepTime >= stepInterval) {
      digitalWrite(STEP_PIN, HIGH);
      delayMicroseconds(50);
      digitalWrite(STEP_PIN, LOW);
      lastStepTime = millis();
    }
  }

  // DEBUG
  Serial.print("Pot: "); Serial.print(potVal);
  Serial.print(" | FSR: "); Serial.print(fsrVal);
  Serial.print(" | PWM: "); Serial.print(motorPWM);
  Serial.print(" | MotorDir: "); Serial.println(motorDir);
}
