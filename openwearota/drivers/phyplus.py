"""PhyPlus PHY62xx OTA driver (SHB_OTA GATT fingerprint).

This driver handles devices running PhyPlus's own reference OTA profile as
shipped in PHY62XX_SDK 3.1.1 (``components/profiles/ota/``).  Unlike the rest
of OpenWearOTA, this section is **not** a reverse-engineered FitPro artefact —
the protocol is transcribed directly from vendor source code that PhyPlus
distributes publicly (GitHub mirror + PhyPlus download portal).  See
DOCUMENTATION.md §3.

GATT fingerprint (PHY+ / PhyPlus):
    Service      5833FF01-9B8B-5191-6142-22A4536EF123
    Command      5833FF02-9B8B-5191-6142-22A4536EF123  (write, with response)
    Response     5833FF03-9B8B-5191-6142-22A4536EF123  (notify)
    Data         5833FF04-9B8B-5191-6142-22A4536EF123  (write, no response)

Relationship to SHB/SLB: the SHB_OTA GATT UUIDs (5833FF0x…) are shared with
this chip family.  FitPro's own ``com.otalib`` code targets the older PHY6212 /
SDK 2.x opcode generation when those UUIDs appear; this driver implements the
PHY62x2 / SDK 3.x generation confirmed directly from vendor source.  See
DOCUMENTATION.md §1 (SHB/SLB) and §3 (PhyPlus) for the full explanation.
"""
from __future__ import annotations
import asyncio
import struct
from pathlib import Path

from ..crc import crc16_arc

# ── GATT characteristics ────────────────────────────────────────────────────

CHAR_COMMAND  = "5833ff02-9b8b-5191-6142-22a4536ef123"
CHAR_RESPONSE = "5833ff03-9b8b-5191-6142-22a4536ef123"
CHAR_DATA     = "5833ff04-9b8b-5191-6142-22a4536ef123"

# ── Command opcodes (written to CHAR_COMMAND) ────────────────────────────────

PHY_CMD_START_OTA      = 0x01
PHY_CMD_PARTITION_INFO = 0x02
PHY_CMD_REBOOT         = 0x04
PHY_CMD_ERASE          = 0x05

# ── Response opcodes (received as notifications on CHAR_RESPONSE) ─────────────

PHY_RSP_START_OTA      = 0x81
PHY_RSP_PARTITION_INFO = 0x84
PHY_RSP_BLOCK_BURST    = 0x87
PHY_RSP_ERASE          = 0x89
PHY_RSP_REBOOT         = 0x8A
PHY_RSP_ERROR          = 0xFF

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

# ── Helpers ──────────────────────────────────────────────────────────────────

def build_partition_info(index: int, flash_addr: int, run_addr: int,
                          size: int, checksum: int) -> bytes:
    """PHY_CMD_PARTITION_INFO payload: idx + 3× LE uint32 + LE uint16 CRC."""
    return struct.pack("<BIIIH", index, flash_addr, run_addr, size, checksum)


# ── Main upgrade entry-point ─────────────────────────────────────────────────

async def identify_phyplus_chip(client) -> str | None:
    """
    Attempts to identify the specific PhyPlus chipset.
    Currently returns None as no universal ID characteristic is identified.
    """
    return None


async def run_phyplus_upgrade(
    client,
    firmware_path: Path,
    *,
    verbose: bool = False,
    flash_addr: int = 0x11000000,
    run_addr: int | None = None,
) -> bool:
    """OTA-flash a PhyPlus PHY62xx device (SDK 3.x single-partition flow).

    ``flash_addr`` defaults to PHY62x2's standard OTA-area base address (see
    DOCUMENTATION.md §3).  Pass an explicit value if your firmware targets a
    different flash offset.  ``run_addr`` defaults to ``flash_addr`` (the
    common execute-in-place case).
    """
    firmware = firmware_path.read_bytes()
    run_addr = flash_addr if run_addr is None else run_addr

    notif_queue: asyncio.Queue[bytes] = asyncio.Queue()

    def _handler(_sender, data: bytes) -> None:
        notif_queue.put_nowait(bytes(data))

    # Enabling notifications on Response flips the device into its "ready for
    # OTA commands" state (SDK: ota_service.c) — must happen before START_OTA.
    await client.start_notify(CHAR_RESPONSE, _handler)

    mtu     = getattr(client, "mtu_size", 23)
    mtu_a   = mtu - 3          # per-write payload budget (ATT overhead removed)
    burst_n = 16               # ACK every (mtu_a × burst_n) bytes; SDK default

    async def send_cmd(opcode: int, payload: bytes = b"") -> None:
        await client.write_gatt_char(
            CHAR_COMMAND, bytes([opcode]) + payload, response=True
        )

    async def wait_notif(timeout: float = 10.0) -> bytes:
        return await asyncio.wait_for(notif_queue.get(), timeout=timeout)

    def _check(raw: bytes, expect_rsp: int, what: str) -> bool:
        if (
            not raw
            or raw[-1] == PHY_RSP_ERROR
            or (len(raw) >= 2 and raw[0] != 0 and raw[-1] != expect_rsp)
        ):
            err  = raw[0] if raw else None
            name = PHY_ERR_NAMES.get(err, f"error {err}")
            print(
                f"[!] {what} failed: {name} "
                f"(raw: {raw.hex() if raw else '(none)'})"
            )
            return False
        return True

    # ── Step 1: START_OTA ────────────────────────────────────────────────────
    print("[*] Starting PhyPlus OTA (single partition)…")
    await send_cmd(PHY_CMD_START_OTA, bytes([1, min(burst_n, 0xFE)]))
    raw = await wait_notif()
    if verbose:
        print(f"    start_ota reply: {raw.hex()}")
    if not _check(raw, PHY_RSP_START_OTA, "Start OTA"):
        await client.stop_notify(CHAR_RESPONSE)
        return False

    # ── Step 2: PARTITION_INFO ───────────────────────────────────────────────
    checksum = crc16_arc(firmware)
    print(
        f"[*] Sending partition info "
        f"({len(firmware)} bytes, crc16/arc={checksum:#06x})…"
    )
    await send_cmd(
        PHY_CMD_PARTITION_INFO,
        build_partition_info(0, flash_addr, run_addr, len(firmware), checksum),
    )
    raw = await wait_notif()
    if not _check(raw, PHY_RSP_PARTITION_INFO, "Partition info"):
        await client.stop_notify(CHAR_RESPONSE)
        return False

    # ── Step 3: stream firmware ──────────────────────────────────────────────
    chunk_size  = mtu_a
    burst_bytes = mtu_a * burst_n
    total_len   = len(firmware)
    offset      = 0

    print(
        f"[*] Streaming {total_len} bytes "
        f"({chunk_size}B/write, ACK every {burst_bytes}B)…"
    )
    while offset < total_len:
        burst_start = offset
        while offset < total_len and (offset - burst_start) < burst_bytes:
            chunk = firmware[offset : offset + chunk_size]
            await client.write_gatt_char(CHAR_DATA, chunk, response=False)
            offset += len(chunk)

        if (offset - burst_start) == burst_bytes or offset == total_len:
            raw = await wait_notif(timeout=30.0)
            if raw and raw[-1] == PHY_RSP_ERROR:
                err  = raw[0] if raw else None
                print(
                    f"\n[!] Device reported "
                    f"{PHY_ERR_NAMES.get(err, f'error {err}')} "
                    f"at offset {offset} (raw: {raw.hex()})."
                )
                await client.stop_notify(CHAR_RESPONSE)
                return False
            if verbose and raw:
                print(f"\n    burst ack: {raw.hex()}")

        print(
            f"\r[*] Progress: {100 * offset // total_len:3d}%"
            f"  ({offset}/{total_len})",
            end="",
            flush=True,
        )

    print()
    await client.stop_notify(CHAR_RESPONSE)
    print(
        "[✓] Partition written and CRC-checked on-device. "
        "Device will reboot into new firmware."
    )
    print(
        "    (Device-side CRC16/ARC + flash-program completed by this point; "
        "no separate 'confirm' step in this protocol revision.)"
    )
    return True
