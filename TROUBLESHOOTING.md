# Troubleshooting Guide

This guide covers common issues encountered while using OpenWearOTA, ranging from software environment problems to device-specific flashing errors.

## 💻 Program-Level Errors

### Bluetooth & Bleak Issues
- **`'bleak' is not installed`**: 
  Run `pip install bleak`. If you are using a virtual environment, ensure it is activated.
- **Permission Denied (Linux)**: 
  BLE operations often require root or specific capabilities. Try running with `sudo` or adding your user to the `bluetooth` group.
- **No devices found during scan**:
  - Ensure your computer's Bluetooth is turned **ON**.
  - Move the watch closer to the computer.
  - Ensure the watch is not currently connected to your phone (BLE devices often stop advertising when connected).

### Connection Failures
- **`Connecting to ... FAILED`**:
  - The device may have moved out of range.
  - The device might have timed out or rebooted.
  - The device may be in a state where it is no longer accepting connections. Try restarting the watch.

### Python Environment
- **`ModuleNotFoundError`**:
  Ensure all dependencies in `pyproject.toml` are installed via `pip install .` or `pip install -r requirements.txt`.

---

## ⌚ Watch-Level Errors

### Chipset Detection Issues
- **`Could not identify a known chip family`**:
  - The watch may not be in the correct **OTA mode**. Many watches require a specific button combination or a trigger from the original app to enter the flashing state.
  - The chipset may be unsupported. If the program prints the GATT services, you can report these to the developers for potential driver support.
- **`Watch is in normal mode. Trigger OTA-handover first`**:
  - Specifically for SHB/SLB chips: The watch is currently running its main application. You must trigger the handover to the bootloader/OTA service before flashing.

### Flashing & Firmware Errors
- **`MODEL MISMATCH`**:
  - The program detected a difference between the watch's internal Chip ID and the ID embedded in the firmware file.
  - **Danger**: Flashing firmware meant for a different model (e.g., TLSR8251 firmware on a TLSR8232) can permanently brick your device. Verify your firmware source.
- **`OTA did not complete`**:
  - Connection was lost during transfer.
  - The firmware file is corrupted or not formatted correctly for the target SoC.
  - The device rejected the image due to a CRC failure or version mismatch.

### Recovery
- **Bricked Device**:
  - If the watch does not boot after a failed flash, try entering the bootloader manually (if known for your model) to re-flash a known working image.
  - If the device is completely unresponsive, hardware flashing via UART/SWD may be the only remaining option.