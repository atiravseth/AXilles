//pin initiialize
const int button0 = 2; //interrupt pin
const int button1 = 3;
const int redLED = 9;
const int blueLED = 10;
const int greenLED = 11;
const int pot = A0;

//states
bool redState = false;
bool blueState = false;
bool greenState = false;

//button 0: state change
unsigned long pressTimestamp = 0;  // the last time the output pin was toggled
unsigned long debounceDelay = 100;    //  debounce time
bool button0_toggle = false; 
int state0 = 0;

//button 1:
unsigned long pressTimestamp1 = 0;
unsigned long debounceDelay1 = 50;
bool button1_toggle = false; 
int state1 = 0;

//potentiometer
int lastBrightness = -1;

//serial input state
bool state2reset = false;

//state change interrupt service routine
void stateISR() {
  button0_toggle = true; 
}

void setup() 
{
  // put your setup code here, to run once:
  pinMode(redLED, OUTPUT);
  pinMode(blueLED, OUTPUT);
  pinMode(greenLED, OUTPUT);

  digitalWrite(redLED, HIGH);
  digitalWrite(blueLED, HIGH);
  digitalWrite(greenLED, HIGH);

  pinMode(button0, INPUT_PULLUP);
  pinMode(button1, INPUT_PULLUP);

  attachInterrupt(digitalPinToInterrupt(button0), stateISR, FALLING);

  Serial.begin(9600);
}

void loop() {
  if (button0_toggle) {
    button0_toggle = false; // clear flag

    unsigned long now = millis();
    if (now - pressTimestamp > debounceDelay) {
      
      state0++;
      if (state0 == 3) {
        state0 = 0;                       
      }
      
      Serial.print("Current State: ");
      Serial.println(state0);
      pressTimestamp = now;
    }
  }

  //state1
  if (state0 == 0) {
    // the default colour to recognize State 0 is Magenta
    digitalWrite(blueLED, blueState);
    digitalWrite(greenLED, HIGH);
    digitalWrite(redLED, redState);

    unsigned long now = millis();
    
    if (digitalRead(button1) == HIGH && !state1 && (now - pressTimestamp1 > debounceDelay1)) {
      state1 = true;
      redState = !redState;
      blueState = !blueState;
      digitalWrite(redLED, redState);
      digitalWrite(blueLED, blueState);

      Serial.print("LED toggled to state ");
      Serial.println(redState);

      pressTimestamp1 = now;   
      }

    if (digitalRead(button1) == LOW && state1 && (now - pressTimestamp1 > debounceDelay1)) {
      state1 = false;
      pressTimestamp1 = now;
      }

    if (state0 != 2) {
      state2reset = false;
      }
    }

  //state2
  else if (state0 == 1) {
    // the default colour to recognize State 0 is Cyan
    digitalWrite(redLED, HIGH);
    digitalWrite(blueLED, LOW);
    digitalWrite(greenLED, LOW);

    int potValue = analogRead(pot);
    
    int brightness = map(potValue, 0, 1024, 255, 0);
    analogWrite(greenLED, brightness);
    analogWrite(blueLED, brightness);

    if (brightness != lastBrightness) {
    Serial.print("Brightness: ");
    Serial.println(brightness);
    lastBrightness = brightness;
    }

    if (state0 != 2) {
      state2reset = false;
}
  }

  //state3
  else if (state0 == 2) {

    // the default state to recognize State 2 is that the LED is off
    if (!state2reset) {
    digitalWrite(redLED, HIGH);
    digitalWrite(blueLED, HIGH);
    digitalWrite(greenLED, HIGH);
    state2reset = true;
    }

    if (Serial.available() > 0) {
    String input = Serial.readStringUntil('\n');

    input.trim();

    if (input.length() >= 2) {

        char colour = input.charAt(0);  
        
        String brightness = input.substring(input.length() - 3);

        for (int i = 0; i < brightness.length(); i++) {
          if (!isDigit(brightness.charAt(i))) {
              Serial.println("Error: Brightness must be digits");
              return;
          }
        }

        int pwm = brightness.toInt();

        if (pwm < 0 || pwm > 255) {
            Serial.println("Error: PWM input must be between 0 and 255");
            return;
        }

        Serial.print("Colour: ");
        Serial.println(colour);
    
        Serial.print("Brightness: ");
        Serial.println(pwm);

        if (colour == 'r' || colour == 'R') {
            analogWrite(redLED, 255 - pwm);
        }
        
        else if (colour == 'g' || colour == 'G') {
            analogWrite(greenLED, 255 - pwm);
        }
        
        else if (colour == 'b' || colour == 'B') {
            analogWrite(blueLED, 255 - pwm);
        }

        else if (colour == 'c' || colour == 'C') {
            digitalWrite(redLED, HIGH);
            digitalWrite(blueLED, HIGH);
            digitalWrite(greenLED, HIGH);
        }
      }
    }
  }
}
