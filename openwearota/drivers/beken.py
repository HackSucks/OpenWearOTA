from __future__ import annotations
import asyncio
import struct
from pathlib import Path

CHAR_IDENTIFY = "f000ffc1-0451-4000-b000-000000000000"
CHAR_BLOCK    = "f000ffc2-0451-4000-b000-000000000000"

CMD_GET_VERSION   = 1;  CMD_VERSION_REPLY  = 2
CMD_OTA_REQUEST   = 3;  CMD_CAN_OTA_REPLY  = 4
CMD_SEND_BLOCK    = 5;  CMD_RESEND         = 6
CMD_SEND_COMPLETE = 7;  CMD_OTA_DONE       = 8
CMD_BLOCK_LEN_CHG = 9;  CMD_ACK_BLOCK_LEN  = 10
CMD_REBOOT        = 11


def build_beken_frame(cmd_id, frame_seq, payload=b""):
    length = len(payload)
    return bytes([cmd_id, frame_seq & 0xFF, length & 0xFF, (length >> 8) & 0xFF]) + payload


async def identify_beken_chip(client) -> str | None:
    """
    Attempts to identify the specific Beken chipset.
    Currently returns None as no universal ID characteristic is identified.
    """
    return None


async def run_beken_upgrade(client, firmware_path: Path, verbose=False) -> bool:
    firmware  = firmware_path.read_bytes()
    frame_seq = 0
    notif_queue: asyncio.Queue[bytes] = asyncio.Queue()

    def _handler(_sender, data):
        notif_queue.put_nowait(bytes(data))

    await client.start_notify(CHAR_BLOCK, _handler)

    async def wait_notif(timeout=10.0):
        return await asyncio.wait_for(notif_queue.get(), timeout=timeout)

    async def send_cmd(cmd_id, payload=b""):
        nonlocal frame_seq
        await client.write_gatt_char(CHAR_IDENTIFY,
                                     build_beken_frame(cmd_id, frame_seq, payload), response=True)
        frame_seq = (frame_seq + 1) & 0xFF

    print("[*] Requesting device info...")
    await send_cmd(CMD_GET_VERSION)
    raw = await wait_notif()
    bank = raw[6] if len(raw) > 6 else 0
    print(f"    Flash bank: {'upper/app' if bank==1 else 'lower/backup' if bank==2 else str(bank)}")

    print("[*] Sending OTA request (first 32 bytes)...")
    await send_cmd(CMD_OTA_REQUEST, firmware[:32])
    raw = await wait_notif(timeout=15.0)

    if len(raw) > 4 and raw[4] == 1:
        print("[!] Watch refused OTA — firmware header mismatch.")
        await client.stop_notify(CHAR_BLOCK)
        return False

    try:
        start_offset = struct.unpack_from("<I", raw, 4)[0]
        req_length   = struct.unpack_from("<I", raw, 8)[0]
        block_size   = struct.unpack_from("<H", raw, 12)[0]
    except struct.error:
        start_offset, req_length, block_size = 0, len(firmware), 128

    print(f"    Start: {start_offset}, length: {req_length}, block: {block_size}B")

    offset = start_offset
    total  = start_offset + req_length
    print(f"[*] Streaming {req_length} bytes...")

    while offset < total:
        chunk   = firmware[offset:offset+block_size]
        payload = offset.to_bytes(4, "little") + chunk
        frame   = build_beken_frame(CMD_SEND_BLOCK, frame_seq, payload)
        frame_seq = (frame_seq + 1) & 0xFF
        await client.write_gatt_char(CHAR_IDENTIFY, frame, response=False)

        try:
            notif = notif_queue.get_nowait()
            if notif[0] == CMD_RESEND and len(notif) >= 8:
                offset = struct.unpack_from("<I", notif, 4)[0]
                continue
            elif notif[0] == CMD_BLOCK_LEN_CHG:
                block_size = struct.unpack_from("<H", notif, 4)[0] if len(notif) >= 6 else block_size
                ack = build_beken_frame(CMD_ACK_BLOCK_LEN, notif[1])
                await client.write_gatt_char(CHAR_IDENTIFY, ack, response=True)
                continue
        except asyncio.QueueEmpty:
            pass

        offset += len(chunk)
        print(f"\r[*] Progress: {100*(offset-start_offset)//req_length:3d}%", end="", flush=True)

    print()
    print("[*] Sending complete...")
    await send_cmd(CMD_SEND_COMPLETE)
    raw = await wait_notif(timeout=30.0)
    success = len(raw) > 4 and raw[4] == 0

    print("[*] Rebooting...")
    await send_cmd(CMD_REBOOT)
    await client.stop_notify(CHAR_BLOCK)

    if success:
        print("[✓] Beken OTA succeeded.")
        return True
    print("[!] Watch reported failure. Reboot sent regardless.")
    return False