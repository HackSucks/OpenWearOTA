# Prebuilt Images

This directory contains precompiled firmware images (`.bin` and `.hex` files) for various supported SoC models.

## Contents

Images are organized by chipset family. Each prebuilt image provided here:
- **BLE UART**: Includes a BLE-to-UART bridge to allow communication with the device.
- **Python Interpreter**: Includes a Python interpreter (e.g., MicroPython or a similar runtime) for on-device scripting and testing.

## Usage

These images can be flashed using OpenWearOTA:

```bash
openwearota flash <DEVICE_MAC> prebuilts/<CHIPSET>/firmware.bin
```

Verify the chipset of your watch before flashing to avoid bricking the device.