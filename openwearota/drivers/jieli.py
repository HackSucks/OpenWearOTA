from __future__ import annotations
import asyncio
import struct
from pathlib import Path

CHAR_WRITE  = "0000ae01-0000-1000-8000-00805f9b34fb"
CHAR_NOTIFY = "0000ae02-0000-1000-8000-00805f9b34fb"
PREFIX = bytes([0xFE, 0xDC, 0xBA])
END    = 0xEF

CMD_GET_TARGET_INFO   = 0x03
CMD_OTA_GET_OFFSET    = 0xE1
CMD_OTA_INQUIRE       = 0xE2
CMD_OTA_ENTER         = 0xE3
CMD_OTA_EXIT          = 0xE4
CMD_OTA_SEND_BLOCK    = 0xE5
CMD_OTA_REFRESH       = 0xE6
CMD_REBOOT            = 0xE7
CMD_OTA_NOTIFY_SIZE   = 0xE8


def build_rcsp_frame(opcode, payload, sn):
    inner  = bytes([sn, opcode]) + payload
    frame  = bytearray(PREFIX)
    frame += bytes([len(inner) & 0xFF, (len(inner) >> 8) & 0xFF])
    frame += inner
    frame.append(END)
    return bytes(frame)


def parse_rcsp_frame(buf):
    assert buf[:3] == PREFIX
    length = buf[3] | (buf[4] << 8)
    body   = buf[5:5+length]
    assert buf[5+length] == END
    return body[0], body[1], body[2], body[3:]


async def identify_jieli_chip(client) -> str | None:
    """
    Attempts to identify the specific Jieli chipset.
    Currently returns None as no universal ID characteristic is identified.
    """
    return None


async def run_jieli_upgrade(client, firmware_path: Path, verbose=False) -> bool:
    firmware  = firmware_path.read_bytes()
    total_len = len(firmware)
    sn        = 0
    notif_buf: list[bytes] = []
    notif_event = asyncio.Event()

    def _handler(_sender, data):
        notif_buf.append(bytes(data))
        notif_event.set()

    await client.start_notify(CHAR_NOTIFY, _handler)

    async def send(opcode, payload=b""):
        nonlocal sn
        await client.write_gatt_char(CHAR_WRITE, build_rcsp_frame(opcode, payload, sn), response=True)
        sn = (sn + 1) & 0xFF
        notif_event.clear()
        await asyncio.wait_for(notif_event.wait(), timeout=10.0)
        raw = notif_buf.pop(0)
        return parse_rcsp_frame(raw)

    print("[*] Getting device info...")
    _, _, _, payload = await send(CMD_GET_TARGET_INFO)
    if payload:
        try:
            name = payload[1:1+payload[0]].decode("utf-8", errors="replace")
            print(f"    Device name: {name}")
        except Exception:
            print(f"    Info raw: {payload.hex()}")

    print("[*] Checking if device can update...")
    _, _, _, payload = await send(CMD_OTA_INQUIRE)
    if payload and payload[0] != 0:
        print(f"[!] Refused (reason: {payload[0]}). Low battery, wrong product, or in progress.")
        await send(CMD_OTA_EXIT)
        await client.stop_notify(CHAR_NOTIFY)
        return False

    print("[*] Entering update mode...")
    _, _, _, payload = await send(CMD_OTA_ENTER)
    if not payload or payload[0] != 1:
        print("[!] Failed to enter update mode.")
        await client.stop_notify(CHAR_NOTIFY)
        return False

    print(f"[*] Notifying firmware size: {total_len} bytes...")
    await send(CMD_OTA_NOTIFY_SIZE, struct.pack("<II", total_len, 0))

    print("[*] Checking resume offset...")
    _, _, _, payload = await send(CMD_OTA_GET_OFFSET)
    offset     = struct.unpack_from("<I", payload)[0] if len(payload) >= 4 else 0
    chunk_size = struct.unpack_from("<H", payload, 4)[0] if len(payload) >= 6 else 128
    chunk_size = min(chunk_size, getattr(client, "mtu_size", 23) - 10)
    if offset:
        print(f"    Resuming from offset {offset}")

    print(f"[*] Streaming firmware ({total_len} bytes, chunk={chunk_size})...")
    while offset < total_len:
        chunk = firmware[offset:offset+chunk_size]
        _, _, _, rsp = await send(CMD_OTA_SEND_BLOCK, struct.pack("<I", offset) + chunk)
        if len(rsp) >= 4:
            confirmed = struct.unpack_from("<I", rsp)[0]
            if confirmed != offset + len(chunk):
                offset = confirmed
                continue
        offset += len(chunk)
        print(f"\r[*] Progress: {100*offset//total_len:3d}%  ({offset}/{total_len})", end="", flush=True)

    print()
    print("[*] Waiting for verification...")
    for _ in range(30):
        _, _, _, rsp = await send(CMD_OTA_REFRESH)
        if rsp and rsp[0] != 0:
            break
        await asyncio.sleep(1.0)

    print("[*] Rebooting...")
    await send(CMD_REBOOT, b"\x00")
    try:
        await send(CMD_OTA_EXIT)
    except Exception:
        pass

    await client.stop_notify(CHAR_NOTIFY)
    print("[✓] JieLi OTA complete.")
    return True