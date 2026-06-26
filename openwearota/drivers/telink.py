from __future__ import annotations
import asyncio
import math
from pathlib import Path
from ..crc import telink_crc16

CHAR_OTA = "00010203-0405-0607-0809-0a0b0c0d2b12"
CHAR_VERSION = "0000ffd4-0000-1000-8000-00805f9b34fb"

STATUS_NAMES = {
    0: "Success", 1: "Started", 2: "Stopped", 4: "Busy", 5: "Rebooting",
    16: "FAIL: bad parameters", 17: "FAIL: connection interrupted",
    18: "FAIL: battery too low — charge the watch first",
    19: "FAIL: version compare error (monotonic versioning enforced)",
    20: "FAIL: packet sent error", 21: "FAIL: packet sent timeout",
    22: "FAIL: flow timeout", 23: "FAIL: reconnect error",
    24: "FAIL: device not connected", 25: "FAIL: service not found",
    26: "FAIL: characteristic not found",
}


def build_telink_packet(index: int, chunk: bytes) -> bytes:
    pkt = bytearray([0xFF] * 20)
    pkt[0] = index & 0xFF
    pkt[1] = (index >> 8) & 0xFF
    pkt[2:2+len(chunk)] = chunk
    crc = telink_crc16(bytes(pkt[0:18]))
    pkt[18] = crc & 0xFF
    pkt[19] = (crc >> 8) & 0xFF
    return bytes(pkt)


async def identify_telink_chip(client) -> str | None:
    """
    Reads the version characteristic to identify the specific Telink chipset model.
    Extracts 4 bytes starting from index 2, matching FitPro's OtaPacketParser logic.
    """
    try:
        data = await client.read_gatt_char(CHAR_VERSION)
        if len(data) >= 6:
            chip_id = data[2:6]
            return chip_id.hex().upper()
    except Exception:
        pass
    return None


def build_telink_end_packet(next_index: int) -> bytes:
    payload = bytearray([0xFF] * 16)
    idx = bytes([next_index & 0xFF, (next_index >> 8) & 0xFF])
    body = idx + bytes(payload)
    crc = telink_crc16(body)
    return body + bytes([crc & 0xFF, (crc >> 8) & 0xFF])


async def run_telink_upgrade(client, firmware_path: Path, pace_ms=8, verbose=False) -> bool:
    firmware = firmware_path.read_bytes()
    print(f"[*] Firmware version bytes: {firmware[2:6].hex()}")

    notif_queue: asyncio.Queue[bytes] = asyncio.Queue()

    def _handler(_sender, data):
        notif_queue.put_nowait(bytes(data))

    await client.start_notify(CHAR_OTA, _handler)
    total_chunks = math.ceil(len(firmware) / 16)
    pace = pace_ms / 1000.0

    print(f"[*] Sending {total_chunks} packets...")
    for idx in range(total_chunks):
        chunk = firmware[idx*16:(idx+1)*16]
        await client.write_gatt_char(CHAR_OTA, build_telink_packet(idx, chunk), response=False)
        if pace > 0:
            await asyncio.sleep(pace)
        print(f"\r[*] Progress: {100*(idx+1)//total_chunks:3d}%  ({idx+1}/{total_chunks})", end="", flush=True)

    print()
    print("[*] Sending end sentinel...")
    await client.write_gatt_char(CHAR_OTA, build_telink_end_packet(total_chunks), response=False)

    print("[*] Waiting for status (up to 5 min)...")
    try:
        raw = await asyncio.wait_for(notif_queue.get(), timeout=300.0)
    except asyncio.TimeoutError:
        await client.stop_notify(CHAR_OTA)
        print("[!] Timed out waiting for status.")
        return False

    await client.stop_notify(CHAR_OTA)
    status = raw[0] if raw else 0xFF
    name   = STATUS_NAMES.get(status, f"Unknown 0x{status:02X}")

    if status < 16:
        print(f"[✓] Status: {name}")
        return True
    print(f"[!] OTA FAILED — {name}")
    return False