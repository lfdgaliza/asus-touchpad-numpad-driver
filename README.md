# ASUS Zenbook 14 UX3405MA Touchpad Numpad Driver

**Security Notice**: This driver requires root privileges to access input devices and I2C bus. It has been updated with security fixes to prevent command injection and other vulnerabilities. Only install from trusted sources.

This is a tailored Python service that enables numpad functionality on the ASUS Zenbook 14 UX3405MA touchpad. The driver is specifically configured for this model with QWERTY keyboard layout and includes security hardening.

## Features

- Converts touchpad into a numeric keypad when numlock is activated
- Top-right corner tap toggles numlock mode
- Top-left corner tap launches calculator
- Supports brightness control (single level for UX3405MA)
- Security-hardened with input validation and safe subprocess calls

## Installation

1. Install required packages:
```bash
sudo apt install libevdev2 python3-libevdev i2c-tools
```

2. Run the installation script:
```bash
sudo ./install.sh
```

3. The service will start automatically and run at boot

## Usage

- **Toggle numpad mode**: Tap the top-right corner of the touchpad
- **Calculator**: Tap the top-left corner (when not in numpad mode)
- **Brightness control**: Tap top-left corner (when in numpad mode)
- **Numpad layout**: Standard calculator layout with % (mapped to 5 key) and = symbols

## Code Quality

- **Constants**: All magic numbers replaced with named constants
- **Structure**: Clean separation of concerns with dedicated functions
- **Error handling**: Comprehensive validation and error messages
- **Documentation**: Inline comments and module docstring
- **Maintainability**: Consistent code style and organization

## Security Features

- **I2C Command Safety**: Hex values are documented hardware protocol bytes, not executable code
- **Input Validation**: Device IDs validated as integers, brightness values from trusted lists
- **Command Injection Prevention**: All I2C commands built using argument lists, never shell strings
- **No Dynamic Code**: Hardware commands are static constants, no code generation or eval()
- **Root Safety**: Service runs with minimal privileges, no network access or file system writes

Now you can get the latest ASUS Touchpad Numpad Driver for Linux from Git and install it using the following commands.
```
git clone https://github.com/mohamed-badaoui/asus-touchpad-numpad-driver
cd asus-touchpad-numpad-driver
sudo ./install.sh
```

To turn on/off numpad, tap top right corner touchpad area.
To adjust numpad brightness, tap top left corner touchpad area.

To uninstall, just run:
```
sudo ./uninstall.sh
```

**Troubleshooting**

To activate logger, do in a console:
```
LOG=DEBUG sudo -E ./asus_touchpad.py
```

For some operating systems with boot failure (Pop!OS, Mint, ElementaryOS, SolusOS), before installing, please uncomment in the asus_touchpad.service file, this following property and adjust its value:
```
# ExecStartPre=/bin/sleep 2
```


It is an adaptation made thanks to:
 - solution published on reddit (https://www.reddit.com/r/linuxhardware/comments/f2vdad/a_service_handling_touchpad_numpad_switching_for/) 
 - many contributions on launchpad (https://bugs.launchpad.net/ubuntu/+source/linux/+bug/1810183)

For any question, please do not hesitate to follow this tread discussion
(https://bugs.launchpad.net/ubuntu/+source/linux/+bug/1810183)

Thank you very much to all the contributors, mainly on launchpad, who made this device driver possible. (Kawaegle, David/magellan-2000, Pilot6/hanipouspilot, Julian Oertel /clunphumb, YannikSc and so many others. GG!)

