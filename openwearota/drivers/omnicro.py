from __future__ import annotations
import asyncio
from pathlib import Path

CHAR_OTA_TX_CMD  = "0000ff01-0000-1000-8000-00805f9b34fb"
CHAR_OTA_RX_CMD  = "0000ff03-0000-1000-8000-00805f9b34fb"
CHAR_ISP_TX = "6e40ff02-b5a3-f393-e0a9-e50e24dcca9e"
CHAR_ISP_RX = "6e40ff03-b5a3-f393-e0a9-e50e24dcca9e"


async def identify_omnicro_chip(client) -> str | None:
    """
    Attempts to identify the specific OnMicro chipset.
    Currently returns None as no universal ID characteristic is identified.
    """
    return None


async def run_onmicro_upgrade(client, firmware_path: Path, verbose=False) -> bool:
    print("[!] OnMicro OTA support is PARTIAL — detect + best-effort only.")
    print("    Full block ACK/retry loop needs a real FitPro HCI capture to verify.\n")

    notif_buf: list[bytes] = []
    notif_event = asyncio.Event()

    def _handler(_sender, data):
        notif_buf.append(bytes(data))
        notif_event.set()

    try:
        await client.start_notify(CHAR_ISP_RX, _handler)
        chip_info = bytes([0x10,0x02,0x10,0x10,0x07,0x01,0x00,0x00,0x00,0x00,0x00,0x00,0x10,0x03,0x16])
        await client.write_gatt_char(CHAR_ISP_TX, chip_info, response=False)
        notif_event.clear()
        try:
            await asyncio.wait_for(notif_event.wait(), timeout=5.0)
            print(f"    Chip info reply: {notif_buf[0].hex()}")
        except asyncio.TimeoutError:
            print("    No reply from ISP channel.")
        await client.stop_notify(CHAR_ISP_RX)
    except Exception:
        pass

    try:
        await client.start_notify(CHAR_OTA_RX_CMD, _handler)
        await client.write_gatt_char(CHAR_OTA_TX_CMD, bytes([0x64,0x00,0x00,0x00]), response=True)
        print("[!] OTA mode entered. Full transfer not implemented.")
        print("    Capture a FitPro HCI snoop log and open a GitHub issue to help finish this.")
        await client.stop_notify(CHAR_OTA_RX_CMD)
    except Exception as e:
        print(f"[!] Could not enter OTA mode: {e}")

    return False