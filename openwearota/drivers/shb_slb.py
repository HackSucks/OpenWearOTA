from __future__ import annotations
import asyncio
import re
import struct
from pathlib import Path
from ..crc import crc16_xmodem, crc16_arc

OP_REQUEST_FW_VERSION  = 0x20
OP_RESPONSE_FW_VERSION = 0x21
OP_UPGRADE_REQUEST     = 0x22
OP_UPGRADE_RESPONSE    = 0x23
OP_DATA_CHECK          = 0x24
OP_SEND_COMPLETE       = 0x25
OP_VERIFY_RESULT       = 0x26
OP_SEND_DATA           = 0x2F

# --- SHB_OTA (PHY62x2-generation) command/response opcodes ---------------
# Confirmed against PhyPlus PHY62XX_SDK 3.1.1, components/profiles/ota/.
# This is a different opcode generation than the FitPro-derived partition
# flow in DOCUMENTATION.md sec 1.8 (which is believed to target the older
# PHY6212 / SDK 2.x generation) -- same GATT fingerprint (Command/Response/
# Data on ...FF02/FF03/FF04), different wire format. See DOCUMENTATION.md
# sec 1.8b.
PHY_CMD_START_OTA       = 0x01
PHY_CMD_PARTITION_INFO  = 0x02
PHY_CMD_BLOCK_INFO      = 0x03  # reserved/unused by the stock SDK flow
PHY_CMD_REBOOT          = 0x04
PHY_CMD_ERASE           = 0x05

PHY_RSP_START_OTA           = 0x81
PHY_RSP_PARTITION_INFO      = 0x84
PHY_RSP_BLOCK_BURST         = 0x87
PHY_RSP_ERASE               = 0x89
PHY_RSP_REBOOT              = 0x8A
PHY_RSP_ERROR                = 0xFF

PHY_ERR_NAMES = {
    100: "invalid OTA state",
    101: "bad data size",
    102: "CRC mismatch",
    103: "no application data",
    104: "bad application data",
    105: "unknown command",
    106: "crypto verify failed",
    107: "security key verify failed",
    108: "double-confirm security failure",
    109: "MIC checksum mismatch",
}


def build_phy_partition_info(index, flash_addr, run_addr, size, checksum):
    """PHY_CMD_PARTITION_INFO payload: idx + 3x LE uint32 + LE uint16 checksum."""
    return struct.pack("<BIIIH", index, flash_addr, run_addr, size, checksum)


def build_slb_frame(seq, opcode, payload, total_packets, packet_index, mtu=517):
    n = min(len(payload), mtu - 7)
    frame = bytearray(4 + n)
    frame[0] = seq & 0x0F
    frame[1] = opcode
    frame[2] = (((total_packets - 1) & 0x0F) << 4) | (packet_index & 0x0F)
    frame[3] = n
    frame[4:4+n] = payload[:n]
    return bytes(frame)


def build_upgrade_request(firmware, res_config_address=None):
    crc  = crc16_xmodem(firmware)
    length = len(firmware)
    flag = 0x01 if res_config_address is not None else 0x00
    addr = res_config_address or 0
    payload  = struct.pack("<B", flag)
    payload += struct.pack("<I", addr)
    payload += struct.pack("<I", length)
    payload += struct.pack("<H", crc)
    payload += b"\x00"
    return payload


_RES_NAME_RE = re.compile(r"(?i)res_([0-9a-f]{8})\.bin$")


def parse_slb_file(path: Path):
    data = path.read_bytes()
    m = _RES_NAME_RE.search(path.name)
    return (data, int(m.group(1), 16)) if m else (data, None)


def is_secure_firmware(path: Path) -> bool:
    return "hexe16" in path.name.lower()


async def identify_shb_slb_chip(client) -> str | None:
    """
    Attempts to identify the specific SHB/SLB chipset.
    Currently returns None as no universal ID characteristic is identified.
    """
    return None


async def run_slb_upgrade(client, firmware_path: Path, verbose=False) -> bool:
    CHAR_WRITE_RSP  = "0000fed5-0000-1000-8000-00805f9b34fb"
    CHAR_WRITE_NORSP = "0000fed7-0000-1000-8000-00805f9b34fb"
    CHAR_NOTIFY     = "0000fed8-0000-1000-8000-00805f9b34fb"

    firmware, res_addr = parse_slb_file(firmware_path)

    if is_secure_firmware(firmware_path):
        print("[!] AES-secured firmware (hexe16) — cannot flash without the per-product key.")
        return False

    notif_queue: asyncio.Queue[bytes] = asyncio.Queue()

    def _handler(_sender, data):
        notif_queue.put_nowait(bytes(data))

    await client.start_notify(CHAR_NOTIFY, _handler)
    mtu = getattr(client, "mtu_size", 23)
    seq = 0

    async def send_cmd(opcode, payload=b""):
        nonlocal seq
        frame = build_slb_frame(seq, opcode, payload, 1, 0, mtu)
        seq = (seq + 1) & 0x0F
        await client.write_gatt_char(CHAR_WRITE_RSP, frame, response=True)

    async def wait_notif(timeout=10.0):
        return await asyncio.wait_for(notif_queue.get(), timeout=timeout)

    print("[*] Requesting firmware version...")
    await send_cmd(OP_REQUEST_FW_VERSION, b"\x00")
    raw = await wait_notif()
    if verbose:
        print(f"    Version reply: {raw.hex()}")

    print("[*] Sending upgrade request...")
    await send_cmd(OP_UPGRADE_REQUEST, build_upgrade_request(firmware, res_addr))

    raw = await wait_notif()
    if len(raw) < 5 or raw[4] != 1:
        print(f"[!] Watch rejected upgrade (raw: {raw.hex()}) — firmware product-ID mismatch?")
        return False

    packets_per_burst = (raw[9] & 0x0F) + 1 if len(raw) > 9 else 1
    chunk_size = mtu - 7
    print(f"[*] Accepted. Burst size: {packets_per_burst}, chunk: {chunk_size}B")

    total_len = len(firmware)
    offset = 0
    burst_idx = 0

    print(f"[*] Streaming {total_len} bytes...")
    while offset < total_len:
        for _ in range(packets_per_burst):
            if offset >= total_len:
                break
            chunk = firmware[offset:offset + chunk_size]
            frame = build_slb_frame(seq, OP_SEND_DATA, chunk, packets_per_burst,
                                    burst_idx % packets_per_burst, mtu)
            seq = (seq + 1) & 0x0F
            burst_idx += 1
            await client.write_gatt_char(CHAR_WRITE_NORSP, frame, response=False)
            offset += len(chunk)

        raw = await wait_notif(timeout=30.0)
        if len(raw) >= 5 and raw[1] == OP_DATA_CHECK:
            confirmed = struct.unpack_from("<I", raw, 1)[0]
            if confirmed != offset:
                offset = confirmed
                burst_idx = offset // chunk_size

        print(f"\r[*] Progress: {100*offset//total_len:3d}%  ({offset}/{total_len})", end="", flush=True)

    print()
    await send_cmd(OP_SEND_COMPLETE, b"\x01")
    raw = await wait_notif(timeout=60.0)
    await client.stop_notify(CHAR_NOTIFY)

    if len(raw) >= 5 and raw[4] == 1:
        print("[✓] Verified — watch will reboot. OTA succeeded.")
        return True
    print(f"[!] Verification failed (raw: {raw.hex()}).")
    return False


async def run_shb_ota_upgrade(client, firmware_path: Path, verbose=False,
                               flash_addr=0x11000000, run_addr=None) -> bool:
    """SHB_OTA mode (watch already rebooted into its OTA-capable bootloader).

    Protocol confirmed against PhyPlus PHY62XX_SDK 3.1.1 (components/profiles/
    ota/), not the FitPro APK. Single-partition flow only -- the vast majority
    of consumer firmware images for these chips are one flat partition; multi-
    partition images are not handled here. flash_addr defaults to PHY62x2's
    OTA-area base address (see DOCUMENTATION.md sec 1.8b); pass an explicit
    address if your firmware targets a different offset. run_addr defaults to
    flash_addr (the common "execute in place" case).
    """
    CHAR_COMMAND  = "5833ff02-9b8b-5191-6142-22a4536ef123"
    CHAR_RESPONSE = "5833ff03-9b8b-5191-6142-22a4536ef123"
    CHAR_DATA     = "5833ff04-9b8b-5191-6142-22a4536ef123"

    firmware = firmware_path.read_bytes()
    run_addr = flash_addr if run_addr is None else run_addr

    notif_queue: asyncio.Queue[bytes] = asyncio.Queue()

    def _handler(_sender, data):
        notif_queue.put_nowait(bytes(data))

    await client.start_notify(CHAR_RESPONSE, _handler)
    mtu = getattr(client, "mtu_size", 23)
    mtu_a = mtu - 3  # otaProtocol_mtu(): per-write payload budget, ATT overhead removed
    burst_size = 16  # blocks acked every (mtu_a * burst_size) bytes; SDK default

    async def send_cmd(opcode, payload=b""):
        await client.write_gatt_char(CHAR_COMMAND, bytes([opcode]) + payload, response=True)

    async def wait_notif(timeout=10.0):
        return await asyncio.wait_for(notif_queue.get(), timeout=timeout)

    def _check(raw, expect_rsp, what):
        if not raw or raw[-1] == PHY_RSP_ERROR or (len(raw) >= 2 and raw[0] != 0 and raw[-1] != expect_rsp):
            err = raw[0] if raw else None
            name = PHY_ERR_NAMES.get(err, f"error {err}")
            print(f"[!] {what} failed: {name} (raw: {raw.hex() if raw else '(none)'})")
            return False
        return True

    print("[*] Starting OTA (single partition)...")
    await send_cmd(PHY_CMD_START_OTA, bytes([1, min(burst_size, 0xFE)]))
    raw = await wait_notif()
    if verbose:
        print(f"    start_ota reply: {raw.hex()}")
    if not _check(raw, PHY_RSP_START_OTA, "Start OTA"):
        await client.stop_notify(CHAR_RESPONSE)
        return False

    checksum = crc16_arc(firmware)
    print(f"[*] Sending partition info ({len(firmware)} bytes, crc16/arc={checksum:#06x})...")
    await send_cmd(PHY_CMD_PARTITION_INFO,
                   build_phy_partition_info(0, flash_addr, run_addr, len(firmware), checksum))
    raw = await wait_notif()
    if not _check(raw, PHY_RSP_PARTITION_INFO, "Partition info"):
        await client.stop_notify(CHAR_RESPONSE)
        return False

    chunk_size = mtu_a
    burst_bytes = mtu_a * burst_size
    total_len = len(firmware)
    offset = 0

    print(f"[*] Streaming {total_len} bytes ({chunk_size}B/write, ack every {burst_bytes}B)...")
    while offset < total_len:
        burst_start = offset
        while offset < total_len and offset - burst_start < burst_bytes:
            chunk = firmware[offset:offset + chunk_size]
            await client.write_gatt_char(CHAR_DATA, chunk, response=False)
            offset += len(chunk)

        if offset - burst_start == burst_bytes or offset == total_len:
            raw = await wait_notif(timeout=30.0)
            if raw and raw[-1] == PHY_RSP_ERROR:
                err = raw[0] if raw else None
                print(f"\n[!] Device reported {PHY_ERR_NAMES.get(err, f'error {err}')} "
                      f"at offset {offset} (raw: {raw.hex()}).")
                await client.stop_notify(CHAR_RESPONSE)
                return False
            if verbose and raw:
                print(f"\n    burst ack: {raw.hex()}")

        print(f"\r[*] Progress: {100*offset//total_len:3d}%  ({offset}/{total_len})", end="", flush=True)

    print()
    await client.stop_notify(CHAR_RESPONSE)
    print("[✓] Partition written and CRC-checked on-device. Watch will reboot into new firmware.")
    print("    (Device-side CRC16/ARC + flash-program step has already completed by this point;")
    print("     there is no separate 'confirm' step in this protocol revision.)")
    return True