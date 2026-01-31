from pyfirmata2 import Arduino, util
import time

PORT = 'COM21'   
board = Arduino(PORT)

# --------- START ITERATOR ---------
it = util.Iterator(board)
it.start()

time.sleep(1)

# --------- PINS ---------
# DC Motor
ENA = board.get_pin('d:5:p')
IN1 = board.get_pin('d:7:o')
IN2 = board.get_pin('d:8:o')

# Stepper
STEP = board.get_pin('d:9:o')
DIR = board.get_pin('d:10:o')
ENABLE = board.get_pin('d:11:o')

# Servo
SERVO = board.get_pin('d:6:s')

# Sensors
POT = board.get_pin('a:0:i')
FSR = board.get_pin('a:1:i')
SWITCH = board.get_pin('d:4:i')

ENABLE.write(0)   # LOW = enable stepper
DIR.write(1)

motor_dir = 1

print("Python control started")

# --------- MAIN LOOP ---------
while True:
    pot = POT.read()
    fsr = FSR.read()
    sw = SWITCH.read()

    # Avoid None at startup
    if pot is None or fsr is None or sw is None:
        continue

    # -------- DC MOTOR --------
    pwm = pot          # 0.0 ~ 1.0
    ENA.write(pwm)

    IN1.write(motor_dir)
    IN2.write(1 - motor_dir)

    # -------- SERVO --------
    angle = int(pot * 180)
    SERVO.write(angle)

    # -------- SWITCH --------
    if sw == 0:   # pressed
        motor_dir = 1 - motor_dir
        time.sleep(0.3)  # debounce

    # -------- STEPPER --------
    if fsr > 0.2:
        STEP.write(1)
        time.sleep(0.001)
        STEP.write(0)
        time.sleep(0.001)

    time.sleep(0.01)
