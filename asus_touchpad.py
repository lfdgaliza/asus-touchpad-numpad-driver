#!/usr/bin/env python3


import logging
import math
import os
import re
import shutil
import subprocess
import sys
from fcntl import F_SETFL, fcntl
from time import sleep, time
from typing import Optional

from libevdev import EV_ABS, EV_KEY, EV_SYN, Device, InputEvent

# Constants
LOG_FORMAT = '%(levelname)s: %(message)s'
DEFAULT_LOG_LEVEL = 'INFO'

# Device detection constants
DEVICE_NOT_FOUND = 0
DEVICE_NAME_FOUND = 1
DEVICE_HANDLER_FOUND = 2

# Touchpad layout constants
NUMPAD_COLS = 5
NUMPAD_ROWS = 4
TOP_OFFSET_RATIO = 0.3

# Performance tuning
DETECTION_ATTEMPTS = 3
MAIN_LOOP_SLEEP = 0.01

# I2C command constants for ASUS touchpad LED control
# These are standard I2C protocol bytes - no executable code, just hardware commands
I2C_COMMAND_HEADER = ["0x05", "0x00", "0x3d", "0x03", "0x06", "0x00",
                      "0x07", "0x00", "0x0d", "0x14", "0x03"]
I2C_COMMAND_FOOTER = "0xad"
I2C_DEVICE_ADDRESS = "0x15"
I2C_WRITE_SIZE = "w13@"

# Complete base command template (safe - no shell interpretation)
I2C_BASE_CMD_TEMPLATE = ["i2ctransfer", "-f", "-y", "{device_id}",
                        I2C_WRITE_SIZE + I2C_DEVICE_ADDRESS] + I2C_COMMAND_HEADER

# Helper function to build safe I2C commands
def build_i2c_command(device_id, brightness_value):
    """Build I2C command with validated inputs to prevent injection."""
    # device_id is already validated as integer, brightness_value is from trusted list
    cmd = [part.format(device_id=device_id) for part in I2C_BASE_CMD_TEMPLATE]
    cmd.append(brightness_value)
    cmd.append(I2C_COMMAND_FOOTER)
    return cmd

# Numpad key layout for UX3402ZA
NUMPAD_KEYS = [
    [EV_KEY.KEY_KP7, EV_KEY.KEY_KP8, EV_KEY.KEY_KP9, EV_KEY.KEY_KPSLASH, EV_KEY.KEY_BACKSPACE],
    [EV_KEY.KEY_KP4, EV_KEY.KEY_KP5, EV_KEY.KEY_KP6, EV_KEY.KEY_KPASTERISK, EV_KEY.KEY_BACKSPACE],
    [EV_KEY.KEY_KP1, EV_KEY.KEY_KP2, EV_KEY.KEY_KP3, EV_KEY.KEY_KPMINUS, EV_KEY.KEY_5],
    [EV_KEY.KEY_KP0, EV_KEY.KEY_KPDOT, EV_KEY.KEY_KPENTER, EV_KEY.KEY_KPPLUS, EV_KEY.KEY_KPEQUAL]
]

# Brightness levels (two levels for ASUS NumberPad LED)
BRIGHTNESS_LEVELS = [1, 2]
BRIGHTNESS_VALUES = [hex(val) for val in BRIGHTNESS_LEVELS]

# Long-press / swipe detection (for top-left/right icons)
LONG_PRESS_SECONDS = 0.7
SWIPE_THRESHOLD_RATIO = 0.1  # fraction of touchpad dimension

# Device identification patterns
# NOTE: touchpad name can vary between models (ASUP/ASUE/ELAN etc.)
TOUCHPAD_PATTERNS = [
    lambda line: "Touchpad" in line,  # broad match for touchpad devices
    lambda line: "ASUE" in line and "Touchpad" in line,
    lambda line: "ELAN" in line and "Touchpad" in line
]

KEYBOARD_PATTERNS = [
    lambda line: "AT Translated Set 2 keyboard" in line,
    lambda line: "Asus Keyboard" in line
]

# Pre-compile regex for performance
DEVICE_ID_PATTERN = re.compile(r".*i2c-(\d+)/.*$")

# Setup logging
# LOG=DEBUG sudo -E ./asus_touchpad-numpad-driver  # all messages
# LOG=ERROR sudo -E ./asus_touchpad-numpad-driver  # only error messages
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
log = logging.getLogger('Pad')
log.setLevel(os.environ.get('LOG', DEFAULT_LOG_LEVEL))

# Pre-check if debug logging is enabled for performance
debug_enabled = log.isEnabledFor(logging.DEBUG)


# Select model from command line (hardcoded for UX3405MA)
model = 'ux3402za'  # ASUS Zenbook 14 UX3405MA uses UX3402ZA layout

# Figure out devices from devices file

touchpad = None
keyboard = None
device_id = None

try_times = DETECTION_ATTEMPTS

while try_times > 0:
    keyboard_detected = 0
    touchpad_detected = 0

    try:
        with open('/proc/bus/input/devices', 'r') as f:
            content = f.read()  # Read all at once instead of line by line
            lines = content.splitlines()
    except IOError:
        log.error("Cannot read /proc/bus/input/devices")
        sys.exit(1)

    for line in lines:
        # Look for the touchpad - optimized string matching
        if touchpad_detected == 0 and any(pattern(line) for pattern in TOUCHPAD_PATTERNS):
            touchpad_detected = 1
            log.debug('Detect touchpad from %s', line.strip())

        elif touchpad_detected == 1:
            if line.startswith("S: "):
                # Use pre-compiled regex
                match = DEVICE_ID_PATTERN.search(line)
                if match:
                    device_id = match.group(1)
                    log.debug('Set touchpad device id %s from %s', device_id, line.strip())

            elif line.startswith("H: "):
                touchpad = line.split("event")[1].split()[0]
                touchpad_detected = 2
                log.debug('Set touchpad id %s from %s', touchpad, line.strip())

        # Look for the keyboard - optimized string matching
        if keyboard_detected == 0 and (("AT Translated Set 2 keyboard" in line) or ("Asus Keyboard" in line)):
            keyboard_detected = 1
            log.debug('Detect keyboard from %s', line.strip())

        elif keyboard_detected == 1 and line.startswith("H: "):
            keyboard = line.split("event")[1].split()[0]
            keyboard_detected = 2
            log.debug('Set keyboard %s from %s', keyboard, line.strip())

        # Early exit if both devices found
        if keyboard_detected == 2 and touchpad_detected == 2:
            break

    if keyboard_detected == 2 and touchpad_detected == 2:
        break

    try_times -= 1
# Validate detected devices
if not all([touchpad, keyboard, device_id]):
    log.error("Failed to detect required devices:")
    if not touchpad:
        log.error("  - Touchpad not found")
    if not keyboard:
        log.error("  - Keyboard not found")
    if not device_id:
        log.error("  - I2C device ID not found")
    sys.exit(1)

if not device_id.isdigit():
    log.error("Invalid device id: %s", device_id)
    sys.exit(1)

device_id = int(device_id)  # Convert to int for safety and performance

# Start monitoring the touchpad

try:
    fd_t = open('/dev/input/event' + str(touchpad), 'rb')
    fcntl(fd_t, F_SETFL, os.O_NONBLOCK)
    d_t = Device(fd_t)
except (OSError, IOError) as e:
    log.error("Failed to open touchpad device /dev/input/event%s: %s", touchpad, e)
    sys.exit(1)


# Retrieve touchpad dimensions #

try:
    ai = d_t.absinfo[EV_ABS.ABS_X]
    (minx, maxx) = (ai.minimum, ai.maximum)
    ai = d_t.absinfo[EV_ABS.ABS_Y]
    (miny, maxy) = (ai.minimum, ai.maximum)
    log.debug('Touchpad min-max: x %d-%d, y %d-%d', minx, maxx, miny, maxy)
except (OSError, KeyError) as e:
    log.error("Failed to get touchpad dimensions: %s", e)
    sys.exit(1)

# Pre-compute values for better performance
maxx_reciprocal = 1.0 / maxx
maxy_reciprocal = 1.0 / maxy
cols_reciprocal = 1.0 / NUMPAD_COLS
rows_reciprocal = 1.0 / NUMPAD_ROWS
top_offset_scaled = TOP_OFFSET_RATIO * NUMPAD_ROWS


# Start monitoring the keyboard (numlock)

try:
    fd_k = open('/dev/input/event' + str(keyboard), 'rb')
    fcntl(fd_k, F_SETFL, os.O_NONBLOCK)
    d_k = Device(fd_k)
except (OSError, IOError) as e:
    log.error("Failed to open keyboard device /dev/input/event%s: %s", keyboard, e)
    sys.exit(1)


# Create a new keyboard device to send numpad events
# KEY_5:6
# KEY_APOSTROPHE:40
# [...]
percentage_key = EV_KEY.KEY_5  # Hardcoded for QWERTY layout (5 key for %)
calculator_key = EV_KEY.KEY_CALC

dev = Device()
dev.name = "Asus Touchpad/Numpad"
dev.enable(EV_KEY.KEY_LEFTSHIFT)
dev.enable(EV_KEY.KEY_NUMLOCK)
dev.enable(calculator_key)

for col in NUMPAD_KEYS:
    for key in col:
        dev.enable(key)

if percentage_key != EV_KEY.KEY_5:
    dev.enable(percentage_key)

try:
    udev = dev.create_uinput_device()
except (OSError, IOError) as e:
    log.error("Failed to create uinput device: %s", e)
    sys.exit(1)


# Brightness configuration (hardcoded for UX3402ZA)
# BRIGHTNESS_VALUES is already set above

I2C_TRANSFER = shutil.which("i2ctransfer") or "i2ctransfer"

def activate_numlock(brightness):
    # Validate brightness index
    if brightness < 0 or brightness >= len(BRIGHTNESS_VALUES):
        log.error("Invalid brightness index: %d", brightness)
        return
    
    # Build command using validated helper function (prevents injection)
    numpad_cmd = build_i2c_command(device_id, BRIGHTNESS_VALUES[brightness])
    log.debug("Activating numlock with brightness %s", BRIGHTNESS_VALUES[brightness])
    events = [
        InputEvent(EV_KEY.KEY_NUMLOCK, 1),
        InputEvent(EV_SYN.SYN_REPORT, 0)
    ]
    udev.send_events(events)
    d_t.grab()
    
    try:
        output = subprocess.check_output(numpad_cmd, stderr=subprocess.STDOUT)
        log.debug("i2ctransfer completed successfully")
    except subprocess.CalledProcessError as e:
        log.error("i2ctransfer failed with code %d. Output: %s", e.returncode, e.output)


def deactivate_numlock():
    # Build command using validated helper function (prevents injection)
    numpad_cmd = build_i2c_command(device_id, "0x00")
    log.debug("Deactivating numlock")
    events = [
        InputEvent(EV_KEY.KEY_NUMLOCK, 0),
        InputEvent(EV_SYN.SYN_REPORT, 0)
    ]
    udev.send_events(events)
    d_t.ungrab()
    subprocess.call(numpad_cmd)


def launch_calculator():
    # Try to open a calculator app if available; fall back to the KEY_CALC keycode.
    for cmd in ("gnome-calculator", "kcalc", "galculator", "xcalc"):
        if shutil.which(cmd):
            try:
                subprocess.Popen([cmd])
                return
            except OSError:
                continue

    try:
        events = [
            InputEvent(calculator_key, 1),
            InputEvent(EV_SYN.SYN_REPORT, 0),
            InputEvent(calculator_key, 0),
            InputEvent(EV_SYN.SYN_REPORT, 0)
        ]
        udev.send_events(events)
    except OSError:
        pass


# status 1 = min bright
# status 2 = middle bright
# status 3 = max bright
def change_brightness(brightness):
    brightness = (brightness + 1) % len(BRIGHTNESS_VALUES)
    # Build command using validated helper function (prevents injection)
    numpad_cmd = build_i2c_command(device_id, BRIGHTNESS_VALUES[brightness])
    log.debug("Changing brightness to level %d (%s)", brightness, BRIGHTNESS_VALUES[brightness])
    subprocess.call(numpad_cmd)
    return brightness


# Run - process and act on events


numlock: bool = False
pos_x: int = 0
pos_y: int = 0
button_pressed = None  # type: Optional[int]
brightness: int = 0

# Touch handling state (for long-press / swipe gestures)
touch_start_time = 0.0
touch_start_x = 0
touch_start_y = 0
touch_area = None  # 'numlock' | 'top_left' | None
numlock_longpress_triggered = False
top_left_longpress_triggered = False

while True:
    # Process all available events
    for e in d_t.events():
        # ...existing code...
        if e.matches(EV_ABS.ABS_MT_POSITION_X):
            x = e.value
            continue
        if e.matches(EV_ABS.ABS_MT_POSITION_Y):
            y = e.value
            continue
        # ...existing code for BTN_TOOL_FINGER...
        if e.matches(EV_KEY.BTN_TOOL_FINGER):
            if e.value == 1 and not button_pressed:
                # ...existing code for finger down...
                log.debug('finger down at x %d y %d', x, y)
                if x < 0 or x > maxx or y < 0 or y > maxy:
                    log.debug('Coordinates out of range: x=%d y=%d (max: %d,%d)', x, y, maxx, maxy)
                    continue
                if (x > 0.95 * maxx) and (y < 0.09 * maxy):
                    touch_area = 'numlock'
                    touch_start_time = time()
                    touch_start_x, touch_start_y = x, y
                    numlock_longpress_triggered = False
                    continue
                elif (x < 0.06 * maxx) and (y < 0.07 * maxy):
                    touch_area = 'top_left'
                    touch_start_time = time()
                    touch_start_x, touch_start_y = x, y
                    top_left_longpress_triggered = False
                    continue
                if not numlock:
                    continue
                col = int(NUMPAD_COLS * x * maxx_reciprocal)
                row = int((NUMPAD_ROWS * y * maxy_reciprocal) - TOP_OFFSET_RATIO)
                if row < 0:
                    continue
                try:
                    button_pressed = NUMPAD_KEYS[row][col]
                except IndexError:
                    log.debug('Unhandled col/row %d/%d for position %d-%d', col, row, x, y)
                    continue
                if button_pressed == EV_KEY.KEY_5:
                    button_pressed = percentage_key
                log.debug('send press key event %s', button_pressed)
                if button_pressed == percentage_key:
                    events = [
                        InputEvent(EV_KEY.KEY_LEFTSHIFT, 1),
                        InputEvent(button_pressed, 1),
                        InputEvent(EV_SYN.SYN_REPORT, 0)
                    ]
                else:
                    events = [
                        InputEvent(button_pressed, 1),
                        InputEvent(EV_SYN.SYN_REPORT, 0)
                    ]
                try:
                    udev.send_events(events)
                except OSError as err:
                    log.warning("Cannot send press event, %s", err)
            elif e.value == 0:
                log.debug('finger up at x %d y %d', x, y)
                if touch_area == 'numlock':
                    touch_area = None
                elif touch_area == 'top_left':
                    duration = time() - touch_start_time
                    dx = abs(x - touch_start_x)
                    dy = abs(y - touch_start_y)
                    dist = max(dx / maxx, dy / maxy)
                    if not top_left_longpress_triggered:
                        if duration >= LONG_PRESS_SECONDS or dist >= SWIPE_THRESHOLD_RATIO:
                            launch_calculator()
                        else:
                            if numlock:
                                brightness = change_brightness(brightness)
                            else:
                                launch_calculator()
                    touch_area = None
                if button_pressed:
                    log.debug('send key up event %s', button_pressed)
                    events = [
                        InputEvent(EV_KEY.KEY_LEFTSHIFT, 0),
                        InputEvent(button_pressed, 0),
                        InputEvent(EV_SYN.SYN_REPORT, 0)
                    ]
                    try:
                        udev.send_events(events)
                        button_pressed = None
                    except OSError as err:
                        log.error("Cannot send release event, %s", err)
                        pass


    # Poll for long-press in numlock area even if no new events
    if touch_area == 'numlock' and not numlock_longpress_triggered:
        duration = time() - touch_start_time
        if duration >= LONG_PRESS_SECONDS:
            numlock = not numlock
            if numlock:
                activate_numlock(brightness)
            else:
                deactivate_numlock()
            numlock_longpress_triggered = True

    # Poll for long-press or swipe in top-left area even if no new events
    if touch_area == 'top_left' and not top_left_longpress_triggered:
        duration = time() - touch_start_time
        dx = abs(x - touch_start_x)
        dy = abs(y - touch_start_y)
        dist = max(dx / maxx, dy / maxy)
        if duration >= LONG_PRESS_SECONDS or dist >= SWIPE_THRESHOLD_RATIO:
            launch_calculator()
            top_left_longpress_triggered = True

    sleep(MAIN_LOOP_SLEEP)  # Optimized for responsiveness
