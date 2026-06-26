#!/usr/bin/env python3
from __future__ import annotations
import argparse
import asyncio
import sys
from pathlib import Path

def _c(code, text): return f"\033[{code}m{text}\033[0m"
def bold(t):   return _c("1",  t)
def green(t):  return _c("32", t)
def yellow(t): return _c("33", t)
def red(t):    return _c("31", t)
def cyan(t):   return _c("36", t)
def dim(t):    return _c("2",  t)

BANNER = r"""
  ___                __        __            ___  _____  _
 / _ \ _ __  ___ _ _\ \      / /__  __ _ _ / _ \|_   _|/ \
| | | | '_ \/ _ \ '_ \ \ /\ / / _ \/ _` | | | | | | |/ _ \
| |_| | |_) \  __/ | | \ V  V /  __/ (_| | |_| | | |/ ___ \
 \___/| .__/ \___|_| |_|\_/\_/ \___|\__,_|\___/  |_/_/   \_\
      |_|
"""

def _print_banner():
    print(cyan(BANNER))
    print(bold("  Re-purpose your e-waste watches via OTA firmware updates."))
    print(dim("  Alpha release — use at your own risk.\n"))

def _print_warning():
    print()
    print(yellow(bold("⚠  WARNING: I AM NOT RESPONSIBLE IF YOU BRICK YOUR WATCH.")))
    print(yellow("   You chose to flash custom firmware onto your watch."))
    print(yellow("   Make sure your firmware is compiled with the SDK for YOUR watch's chipset."))
    print(yellow("   OpenWearOTA will identify the chipset before flashing."))
    print()

def _check_bleak():
    try:
        import bleak  # noqa
    except ImportError:
        print(red("\n[!] 'bleak' is not installed.\n    Fix: pip install bleak\n"))
        sys.exit(1)

async def _scan_devices(timeout=10.0):
    from bleak import BleakScanner
    devices = await BleakScanner.discover(timeout=timeout, return_adv=True)
    found = []
    for addr, (device, adv) in devices.items():
        name = (device.name or "").strip() or "(unnamed)"
        found.append((name, addr, adv.rssi or -999))
    found.sort(key=lambda x: -x[2])
    return found

async def _connect(address, timeout=15.0):
    from bleak import BleakClient
    print(f"  {dim('Connecting to')} {bold(address)} ...", end="", flush=True)
    client = BleakClient(address, timeout=timeout)
    await client.connect()
    if not client.is_connected:
        print(red("  FAILED"))
        return None
    print(green("  connected ✓"))
    if hasattr(client, "request_mtu"):
        try:
            await client.request_mtu(517)
        except Exception:
            pass
    return client

def _get_service_uuids(client):
    return {str(svc.uuid).lower() for svc in client.services}

def _get_all_char_uuids(client):
    uuids = set()
    for svc in client.services:
        for char in svc.characteristics:
            uuids.add(str(char.uuid).lower())
    return uuids

def _print_all_services(client):
    print()
    print(bold("  GATT services:"))
    for svc in client.services:
        print(f"  {cyan('Service')}  {svc.uuid}  {dim(svc.description or '')}")
        for char in svc.characteristics:
            print(f"    {dim('Char')}  {char.uuid}  {dim('[' + ', '.join(char.properties) + ']')}")
    print()

async def _detect_and_report(client):
    from .detect import detect_chip_family, detect_shb_slb_mode, FAMILY_NAMES, SDK_NOTES
    from .drivers.telink   import identify_telink_chip
    from .drivers.beken    import identify_beken_chip
    from .drivers.jieli    import identify_jieli_chip
    from .drivers.realsil   import identify_realsil_chip
    from .drivers.phyplus   import identify_phyplus_chip
    from .drivers.shb_slb   import identify_shb_slb_chip
    from .drivers.omnicro   import identify_omnicro_chip

    service_uuids = _get_service_uuids(client)
    family = detect_chip_family(service_uuids)
    if family is None:
        print(red("\n  [!] Could not identify a known chip family."))
        print(dim("      May not be FitPro-compatible, or needs to enter OTA mode first."))
        _print_all_services(client)
        return None

    print()
    print(f"  {green('Chipset detected:')}  {bold(FAMILY_NAMES[family])}")
    print(f"  {dim(SDK_NOTES[family])}")
    
    # Map family to its identification function
    id_funcs = {
        "telink": identify_telink_chip,
        "beken": identify_beken_chip,
        "jieli": identify_jieli_chip,
        "realsil": identify_realsil_chip,
        "phyplus": identify_phyplus_chip,
        "shb_slb": identify_shb_slb_chip,
        "onmicro": identify_omnicro_chip,
    }

    chip_id = None
    if family in id_funcs:
        chip_id = await id_funcs[family](client)
        if chip_id:
            print(f"  {cyan('Model ID:')}  {bold(chip_id)}")
            
    if family == "shb_slb":
        mode = detect_shb_slb_mode(_get_all_char_uuids(client))
        print(f"  {dim('Sub-mode:')}  {bold(mode)}")
        if mode == "SHB_APP":
            print(yellow("\n  Watch is in normal mode. Trigger OTA-handover first (not yet automated)."))
    print()
    return family, chip_id

async def _do_flash(client, family, fw_path, verbose=False):
    from .detect import detect_shb_slb_mode
    from .drivers.shb_slb import run_slb_upgrade, run_shb_ota_upgrade
    from .drivers.telink  import run_telink_upgrade
    from .drivers.jieli   import run_jieli_upgrade
    from .drivers.beken   import run_beken_upgrade
    from .drivers.onmicro import run_onmicro_upgrade
    from .drivers.realsil import run_realsil_upgrade
    if family == "shb_slb":
        mode = detect_shb_slb_mode(_get_all_char_uuids(client))
        if mode == "SHB_APP":
            print(red("  [!] Watch in normal mode. Enter OTA mode first."))
            return False
        elif mode == "SLB_UPGRADE":
            return await run_slb_upgrade(client, fw_path, verbose=verbose)
        elif mode == "SHB_OTA":
            return await run_shb_ota_upgrade(client, fw_path, verbose=verbose)
        else:
            print(red(f"  [!] Unknown sub-mode: {mode}"))
            return False
    elif family == "telink":  return await run_telink_upgrade(client, fw_path, verbose=verbose)
    elif family == "jieli":   return await run_jieli_upgrade(client, fw_path, verbose=verbose)
    elif family == "beken":   return await run_beken_upgrade(client, fw_path, verbose=verbose)
    elif family == "onmicro": return await run_onmicro_upgrade(client, fw_path, verbose=verbose)
    elif family == "realsil": return await run_realsil_upgrade(client, fw_path, verbose=verbose)
    print(red(f"  [!] No driver for: {family}"))
    return False

def _hr(w=65): print(dim("─" * w))
def _header(t):
    print()
    _hr()
    print(f"  {bold(t).center(63)}")
    _hr()
    print()

def _prompt(text, default=""):
    suffix = f" [{dim(default)}]" if default else ""
    try:
        val = input(f"  {text}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print(); return default
    return val or default

def _pick(options, title="Choose"):
    print(f"  {bold(title)}\n")
    for i, opt in enumerate(options, 1):
        print(f"    {cyan(str(i))}.  {opt}")
    print()
    while True:
        raw = _prompt(f"Enter number (1-{len(options)}, or 0 to go back)", "0")
        if raw == "0": return None
        try:
            idx = int(raw) - 1
            if 0 <= idx < len(options): return idx
        except ValueError:
            pass
        print(red(f"  Please enter a number between 1 and {len(options)}."))

def _list_prebuilts(family):
    from .detect import FAMILY_NAMES
    name = FAMILY_NAMES[family]
    prebuilt_dir = Path("prebuilts") / name.replace(" ", "_").lower()
    if not prebuilt_dir.exists():
        return []
    return [p for p in prebuilt_dir.glob("*") if p.suffix in (".bin", ".hex")]

def _pick_firmware(family=None):
    while True:
        if family:
            prebuilts = _list_prebuilts(family)
            if prebuilts:
                print(f"\n  {cyan('Found prebuilt images for this chipset:')}")
                options = [p.name for p in prebuilts]
                idx = _pick(options, title="Select a prebuilt image (or 0 for custom path)")
                if idx is not None:
                    return prebuilts[idx]
                print(dim("  Using custom path..."))

        raw = _prompt("Path to firmware file (.bin / .hex)")
        if not raw: return None
        p = Path(raw).expanduser()
        if p.exists() and p.is_file(): return p
        print(red(f"  File not found: {p}"))
        if _prompt("Try again? [Y/n]", "y").lower() == "n": return None

async def screen_pick_watch():
    _header("Scan for BLE devices")
    print(f"  {dim('Scanning for 10 seconds — keep the watch close by...')}\n")
    try:
        found = await _scan_devices(timeout=10.0)
    except Exception as e:
        print(red(f"  [!] Scan failed: {e}")); return None
    if not found:
        print(red("  No BLE devices found.")); return None
    labels = []
    for name, addr, rssi in found:
        bar = "▓" * max(1, min(5, (rssi + 100) // 10))
        labels.append(f"{name:<28}  {dim(addr)}  {dim(bar + ' ' + str(rssi) + ' dBm')}")
    idx = _pick(labels, title="Select your watch")
    if idx is None: return None
    name, addr, _ = found[idx]
    return addr, name

async def screen_main_menu():
    while True:
        _header("Main Menu")
        idx = _pick([
            bold("Flash firmware") + dim("   — scan, detect chipset, then OTA flash"),
            bold("UART bridge")    + dim("    — connect to custom/MicroPython firmware REPL"),
            dim("Exit"),
        ], title="What would you like to do?")
        if idx is None or idx == 2:
            print("\n  Goodbye!\n"); return
        elif idx == 0: await screen_flash_flow()
        elif idx == 1: await screen_uart_flow()

async def screen_flash_flow():
    _print_warning()
    result = await screen_pick_watch()
    if result is None: return
    address, name = result
    print(f"\n  {green('Selected:')} {bold(name)}  {dim(address)}")

    _header("Detecting chipset")
    client = await _connect(address)
    if client is None:
        _prompt("Press Enter to return to menu"); return

    try:
        detection = await _detect_and_report(client)
        if detection is None:
            _prompt("Press Enter to return to menu"); return
        family, chip_id = detection

        _header("Select firmware file")
        from .detect import FAMILY_NAMES, SDK_NOTES
        print(f"  Chipset:  {bold(FAMILY_NAMES[family])}")
        print(f"\n  {yellow('Important:')} {SDK_NOTES[family]}\n")

        fw_path = _pick_firmware(family)
        if fw_path is None:
            print("  Cancelled."); return

        size_kb = fw_path.stat().st_size / 1024
        print(f"\n  {green('Firmware:')} {fw_path.name}  {dim(f'({size_kb:.1f} KB)')}")
        
        if family == "telink" and chip_id:
            try:
                fw_data = fw_path.read_bytes()
                if len(fw_data) >= 6:
                    fw_chip_id = fw_data[2:6].hex().upper()
                    if fw_chip_id == chip_id:
                        print(f"  {green('✓ Model Match:')}  Firmware and Watch both report {bold(chip_id)}")
                    else:
                        print(f"  {red('⚠ MODEL MISMATCH:')}")
                        print(f"    Watch: {bold(chip_id)}")
                        print(f"    Firmware: {bold(fw_chip_id)}")
                        print(f"    {red('Flashing the wrong model may brick your device!')}")
            except Exception as e:
                print(dim(f"  Could not verify firmware ID: {e}"))

        print()
        _hr()
        print(yellow("  Are you sure you want to flash this firmware?"))
        print(dim("  If compiled for the wrong chipset, your watch may be bricked."))
        _hr()
        print()
        if _prompt("Type YES to proceed, anything else to cancel", "cancel") != "YES":
            print(dim("  Cancelled — no changes made.")); return

        _header("Flashing firmware")
        ok = await _do_flash(client, family, fw_path)
        print()
        if ok:
            print(green("  ✓ OTA complete! Your watch should reboot with the new firmware."))
        else:
            print(red("  ✗ OTA did not complete. Your watch should be unchanged."))
        print()
    finally:
        try: await client.disconnect()
        except Exception: pass

    _prompt("Press Enter to return to menu")

async def screen_uart_flow():
    from .uart import run_uart_bridge, detect_uart_service, NUS_SERVICE, NUS_TX, NUS_RX
    _header("BLE-UART Bridge")
    print(dim("  Connects to custom/MicroPython firmware over BLE serial (Nordic NUS).\n"))

    result = await screen_pick_watch()
    if result is None: return
    address, name = result
    print(f"\n  {green('Selected:')} {bold(name)}  {dim(address)}")
    print()
    print(dim("  Custom UUIDs? Leave blank for Nordic NUS defaults."))
    svc_uuid = _prompt("Service UUID", NUS_SERVICE)
    tx_uuid  = _prompt("TX UUID (app -> device)", NUS_TX)
    rx_uuid  = _prompt("RX UUID (device -> app)", NUS_RX)

    _header("Connecting")
    client = await _connect(address)
    if client is None:
        _prompt("Press Enter to return to menu"); return

    try:
        if svc_uuid == NUS_SERVICE and not detect_uart_service(_get_service_uuids(client)):
            print(yellow("\n  Nordic UART Service not detected. Use custom UUIDs if your firmware differs."))
            _print_all_services(client)
            _prompt("Press Enter to return to menu"); return
        _hr()
        print(cyan("  UART bridge open. Type below — Ctrl-C to close.\n"))
        await run_uart_bridge(client, service_uuid=svc_uuid, tx_uuid=tx_uuid, rx_uuid=rx_uuid)
    finally:
        try: await client.disconnect()
        except Exception: pass

    _prompt("\nPress Enter to return to menu")

async def cmd_scan_list(_args):
    _header("BLE Device Scan")
    print(dim("  Scanning 10 seconds...\n"))
    found = await _scan_devices()
    if not found:
        print(red("  No devices found.")); return
    print(f"  {'#':<4} {'Name':<28} {'Address':<22} {'RSSI':>6}")
    print(dim("  " + "─" * 62))
    for i, (name, addr, rssi) in enumerate(found, 1):
        print(f"  {dim(str(i)):<4} {name:<28} {addr:<22} {rssi:>5} dBm")
    print()

async def cmd_flash_direct(args):
    fw_path = Path(args.firmware)
    if not fw_path.exists():
        print(red(f"  [!] Not found: {fw_path}")); sys.exit(1)
    _print_warning()
    client = await _connect(args.address)
    if client is None: sys.exit(1)
    try:
        family = await _detect_and_report(client)
        if family is None: sys.exit(1)
        if not args.yes:
            if _prompt("Type YES to proceed", "cancel") != "YES":
                print(dim("  Cancelled.")); return
        ok = await _do_flash(client, family, fw_path, verbose=getattr(args,"verbose",False))
        sys.exit(0 if ok else 1)
    finally:
        try: await client.disconnect()
        except Exception: pass

async def cmd_uart_direct(args):
    from .uart import run_uart_bridge, detect_uart_service, NUS_SERVICE, NUS_TX, NUS_RX
    client = await _connect(args.address)
    if client is None: sys.exit(1)
    try:
        svc = getattr(args,"service_uuid",None) or NUS_SERVICE
        tx  = getattr(args,"tx_uuid",None)      or NUS_TX
        rx  = getattr(args,"rx_uuid",None)      or NUS_RX
        if svc == NUS_SERVICE and not detect_uart_service(_get_service_uuids(client)):
            print(red("  NUS not found. Use --tx-uuid / --rx-uuid.")); sys.exit(1)
        await run_uart_bridge(client, service_uuid=svc, tx_uuid=tx, rx_uuid=rx)
    finally:
        try: await client.disconnect()
        except Exception: pass

def _build_parser():
    p = argparse.ArgumentParser(prog="openwearota",
        description="OpenWearOTA — run without arguments for interactive menu.")
    p.add_argument("--version", action="version", version="OpenWearOTA 0.1.0-alpha")
    sub = p.add_subparsers(dest="command")
    sub.add_parser("scan")
    pf = sub.add_parser("flash")
    pf.add_argument("address"); pf.add_argument("firmware")
    pf.add_argument("-y","--yes", action="store_true")
    pf.add_argument("-v","--verbose", action="store_true")
    pu = sub.add_parser("uart")
    pu.add_argument("address")
    pu.add_argument("--service-uuid", default=None)
    pu.add_argument("--tx-uuid", default=None)
    pu.add_argument("--rx-uuid", default=None)
    return p

def main():
    _print_banner()
    _check_bleak()
    args = _build_parser().parse_args()
    try:
        if   args.command is None:   asyncio.run(screen_main_menu())
        elif args.command == "scan":  asyncio.run(cmd_scan_list(args))
        elif args.command == "flash": asyncio.run(cmd_flash_direct(args))
        elif args.command == "uart":  asyncio.run(cmd_uart_direct(args))
    except KeyboardInterrupt:
        print(dim("\n\n  Interrupted. Goodbye!\n"))

if __name__ == "__main__":
    main()