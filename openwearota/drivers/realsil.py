"""Realsil RTL876x DFU driver.

Protocol reverse-engineered from Realsil's own Android SDK:
  DfuService.java, BinInputStream.java, RealsilDfu.java
  (package com.realsil.android.blehub.dfu, Apache-2.0 licence)

Two-phase process
-----------------
**Phase 1 — App-mode handover (FULL_FUNCTION / normal firmware):**
Connect to the device running normal firmware, write the one-byte enter-OTA
command (0x01) to the OTA_RESET characteristic, then disconnect.  The device
reboots into DFU mode and starts advertising under a *new* MAC address (or the
same address, depending on chip variant).  OpenWearOTA re-scans and reconnects
to the DFU-mode device.

**Phase 2 — DFU transfer:**
Connect to the DFU-mode device (DFU_SERVICE_UUID), exchange control-point
opcodes on CONTROL_POINT (write-with-response + notify), stream image bytes
on DATA (write-no-response), then validate and activate.

This driver supports OTA_MODE_LIMIT_FUNCTION (device is already in DFU mode)
directly, and makes a best-effort attempt at the full handover for
OTA_MODE_FULL_FUNCTION.  The handover scan step is platform-limited in bleak
— if it fails, pass ``limit_function=True`` to skip directly to phase 2.

See DOCUMENTATION.md ?7 for the full protocol reference.
"""
from __future__ import annotations

import asyncio
import struct
from pathlib import Path

# ?? GATT UUIDs (DFU / OTA mode) ?????????????????????????????????????????????

DFU_SERVICE_UUID     = "00006287-3c17-d293-8e48-14fe2e4da212"
DFU_DATA_UUID        = "00006387-3c17-d293-8e48-14fe2e4da212"
DFU_CONTROL_PT_UUID  = "00006487-3c17-d293-8e48-14fe2e4da212"

# Normal-firmware (app-mode) OTA-handover service/characteristic.
# The SDK also mentions an older variant (0000ffd0 / 0000ffd1) but the
# "new spec" service below is what DfuService.java dispatches to first.
OTA_SERVICE_UUID_NEW = "0000d0ff-3c17-d293-8e48-14fe2e4da212"
OTA_SERVICE_UUID_OLD = "0000ffd0-0000-1000-8000-00805f9b34fb"
OTA_RESET_CHAR_UUID  = "0000ffd1-0000-1000-8000-00805f9b34fb"

# ?? Control-point opcodes (written to DFU_CONTROL_PT_UUID) ??????????????????

OP_START_DFU                    = 0x01  # begin transfer  (17-byte payload, AES-encrypted metadata)
OP_RECEIVE_FW_IMAGE             = 0x02  # arm data channel  (7-byte payload: sig + offset)
OP_VALIDATE_FW_IMAGE            = 0x03  # verify after transfer  (3-byte: opcode + sig LE)
OP_ACTIVE_IMAGE_RESET           = 0x04  # activate + reboot  (1 byte)
OP_RESET                        = 0x05  # abort / reset  (1 byte)
OP_REPORT_RECEIVED_IMAGE_INFO   = 0x06  # query current image info  (3-byte: opcode + sig LE)
OP_CONNECTION_PARAMETER_UPDATE  = 0x07  # request faster conn params  (9-byte payload)

# ?? Notification response byte (byte 0 of every notification) ???????????????
#
# DfuService always checks byte[0] == 0x10 before processing; byte[1] is
# the opcode being acked; byte[2] is the status code below.

NOTIFY_RESPONSE_CODE = 0x10  # responseType that marks a valid control-point reply

# ?? DFU status codes (byte[2] of notifications) ?????????????????????????????

DFU_STATUS_SUCCESS              = 0x01
DFU_STATUS_NOT_SUPPORTED        = 0x02
DFU_STATUS_INVALID_PARAM        = 0x03
DFU_STATUS_OPERATION_FAILED     = 0x04
DFU_STATUS_DATA_SIZE_EXCEEDS    = 0x05
DFU_STATUS_CRC_ERROR            = 0x06

DFU_STATUS_NAMES = {
    DFU_STATUS_SUCCESS:          "SUCCESS",
    DFU_STATUS_NOT_SUPPORTED:    "NOT_SUPPORTED",
    DFU_STATUS_INVALID_PARAM:    "INVALID_PARAM",
    DFU_STATUS_OPERATION_FAILED: "OPERATION_FAILED",
    DFU_STATUS_DATA_SIZE_EXCEEDS:"DATA_SIZE_EXCEEDS_LIMIT",
    DFU_STATUS_CRC_ERROR:        "CRC_ERROR",
}

# ?? Connection parameter update constants (from DfuService.java) ?????????????

CONN_INTERVAL_MIN      = 0x0006
CONN_INTERVAL_MAX      = 0x0011
SLAVE_LATENCY          = 0x0000
SUPERVISION_TIMEOUT    = 500

# ?? Bin file header layout ???????????????????????????????????????????????????
#
# Parsed by BinInputStream.parseBinFileHeader() — 12 bytes, all LE:
#   offset      uint16   flash write offset of the image body (relative to app start)
#   signature   uint16   product/image type identifier (must match target's expected sig)
#   version     uint16   firmware version
#   checksum    uint16   simple checksum of the image body
#   length      uint16   image body length in *4-byte units* (so byte_len = length * 4)
#   ota_flag    uint8    OTA flags
#   reserved_8  uint8    reserved
#
# Total header = 12 bytes; image body follows immediately.

BIN_HEADER_SIZE = 12

# ?? MAX_PACKET_SIZE ??????????????????????????????????????????????????????????
#
# DfuService.java hardcodes 20 bytes (the ATT_MTU=23 default minus the 3-byte
# ATT write header).  On modern phones the MTU can be negotiated higher;
# DfuService doesn't do that negotiation.  We follow the same conservative
# default but accept whatever MTU bleak negotiates.

MAX_PACKET_SIZE = 20


# ?? Bin header parser ????????????????????????????????????????????????????????

class BinHeader:
    """Parsed representation of the 12-byte Realsil .bin file header."""

    __slots__ = ("offset", "signature", "version", "checksum",
                 "length", "ota_flag", "reserved_8", "body_size")

    def __init__(self, raw: bytes) -> None:
        if len(raw) < BIN_HEADER_SIZE:
            raise ValueError(f"BIN header too short: {len(raw)} < {BIN_HEADER_SIZE}")
        (self.offset, self.signature, self.version,
         self.checksum, self.length) = struct.unpack_from("<HHHHH", raw, 0)
        self.ota_flag   = raw[10]
        self.reserved_8 = raw[11]
        # length field is in 4-byte units (BinInputStream: "toUnsigned(length)*4")
        self.body_size  = (self.length & 0xFFFF) * 4

    def __repr__(self) -> str:
        return (
            f"BinHeader(offset=0x{self.offset:04x}, sig=0x{self.signature:04x}, "
            f"ver=0x{self.version:04x}, chk=0x{self.checksum:04x}, "
            f"body={self.body_size}B, ota_flag=0x{self.ota_flag:02x})"
        )


# ?? Command builders ?????????????????????????????????????????????????????????

def build_conn_param_update() -> bytes:
    """9-byte connection-parameter-update command (opcode 0x07)."""
    return struct.pack(
        "<BHHHHH",
        OP_CONNECTION_PARAMETER_UPDATE,
        CONN_INTERVAL_MIN,
        CONN_INTERVAL_MAX,
        SLAVE_LATENCY,
        SUPERVISION_TIMEOUT,
        0,  # padding to reach 9 bytes (matches Java array size)
    )[:9]


def build_report_image_info(signature: int) -> bytes:
    """3-byte query-image-info command (opcode 0x06)."""
    return struct.pack("<BH", OP_REPORT_RECEIVED_IMAGE_INFO, signature & 0xFFFF)


def build_start_dfu(hdr: BinHeader, update_offset: int = 0) -> bytes:
    """17-byte START_DFU command (opcode 0x01).

    The 16 metadata bytes (bytes 1-16) are AES-256-ECB encrypted in the
    original SDK.  OpenWearOTA does *not* implement AES encryption — see
    DOCUMENTATION.md ?7.3 for details.  This function builds the *plaintext*
    payload; if your device requires encryption you must supply the AES key
    and encrypt externally.
    """
    payload = struct.pack(
        "<HHHHHBB",
        hdr.offset     & 0xFFFF,
        hdr.signature  & 0xFFFF,
        hdr.version    & 0xFFFF,
        hdr.checksum   & 0xFFFF,
        hdr.length     & 0xFFFF,
        hdr.ota_flag,
        hdr.reserved_8,
    )  # 10 bytes
    payload += b"\x00" * 4  # 4 AES-padding zeros (bytes 11-14 in Java)
    # prepend opcode, trim to 17 bytes total
    return bytes([OP_START_DFU]) + payload[:16]


def build_receive_fw_image(signature: int, update_offset: int) -> bytes:
    """7-byte RECEIVE_FW_IMAGE command (opcode 0x02).

    update_offset == 0 means the whole image (starting after the 12-byte
    header); the Java code substitutes 12 for the offset in that case.
    """
    actual_offset = update_offset if update_offset != 0 else BIN_HEADER_SIZE
    return struct.pack("<BHI", OP_RECEIVE_FW_IMAGE, signature & 0xFFFF, actual_offset)


def build_validate(signature: int) -> bytes:
    """3-byte VALIDATE_FW_IMAGE command (opcode 0x03)."""
    return struct.pack("<BH", OP_VALIDATE_FW_IMAGE, signature & 0xFFFF)


# ?? DFU transfer ?????????????????????????????????????????????????????????????

async def identify_realsil_chip(client) -> str | None:
    """
    Attempts to identify the specific Realtek chipset.
    Currently returns None as no universal ID characteristic is identified.
    """
    return None


async def run_realsil_upgrade(
    client,
    firmware_path: Path,
    *,
    verbose: bool = False,
    limit_function: bool = False,
) -> bool:
    """OTA-flash a Realsil RTL876x device.

    Parameters
    ----------
    client:
        A connected ``bleak.BleakClient``.  The caller should already have
        connected to the device.
    firmware_path:
        Path to the Realsil ``.bin`` firmware file (with 12-byte header).
    verbose:
        Print raw notification bytes for debugging.
    limit_function:
        ``True`` ? device is already in DFU mode (skip phase-1 handover).
        ``False`` ? attempt phase-1 handover first (writes enter-OTA to normal
        firmware, then caller must re-connect to DFU-mode device).  In bleak
        this almost always requires the caller to handle the reconnect loop
        externally; see DOCUMENTATION.md ?7.4.
    """
    raw_firmware = firmware_path.read_bytes()
    if len(raw_firmware) < BIN_HEADER_SIZE:
        print(f"[!] File too small to contain Realsil header ({len(raw_firmware)} bytes).")
        return False

    hdr = BinHeader(raw_firmware)
    print(f"[*] {hdr}")

    body = raw_firmware[BIN_HEADER_SIZE : BIN_HEADER_SIZE + hdr.body_size]
    if len(body) < hdr.body_size:
        print(f"[!] File body shorter than header claims ({len(body)} < {hdr.body_size} bytes).")
        return False

    # ?? Phase 1: handover (normal firmware ? DFU mode) ????????????????????
    if not limit_function:
        print("[*] Phase 1: sending enter-OTA command to normal firmware…")
        entered = await _enter_ota_mode(client, verbose=verbose)
        if not entered:
            print(
                "[!] Could not trigger OTA handover.  If the device is already in "
                "DFU mode, pass limit_function=True (CLI: --limit-function)."
            )
            return False
        print(
            "[!] Device rebooting into DFU mode.  You must re-scan, reconnect "
            "to the DFU-mode device (may have a different MAC address), and "
            "re-run with --limit-function."
        )
        # After sending enter-OTA the device disconnects immediately; we cannot
        # continue in this same bleak session.  Return False so the caller knows
        # to reconnect.
        return False

    # ?? Phase 2: DFU transfer ?????????????????????????????????????????????
    return await _run_dfu_transfer(client, hdr, body, verbose=verbose)


# ?? Phase 1 helper ???????????????????????????????????????????????????????????

async def _enter_ota_mode(client, *, verbose: bool) -> bool:
    """Write the one-byte enter-OTA command to the normal-firmware OTA char."""
    service_uuids = {str(s.uuid).lower() for s in client.services}

    ota_char_uuid: str | None = None
    if OTA_SERVICE_UUID_NEW.lower() in service_uuids:
        ota_char_uuid = OTA_RESET_CHAR_UUID
        if verbose:
            print(f"    Using new OTA service {OTA_SERVICE_UUID_NEW}")
    elif OTA_SERVICE_UUID_OLD.lower() in service_uuids:
        ota_char_uuid = OTA_RESET_CHAR_UUID
        if verbose:
            print(f"    Using legacy OTA service {OTA_SERVICE_UUID_OLD}")

    if ota_char_uuid is None:
        print("[!] Neither OTA handover service found on this device.")
        return False

    try:
        # write-no-response, matches DfuService: WRITE_TYPE_NO_RESPONSE
        await client.write_gatt_char(ota_char_uuid, bytes([0x01]), response=False)
        if verbose:
            print("    Enter-OTA command sent (0x01, write-no-response).")
        await asyncio.sleep(1.0)  # give chip time to reboot into DFU mode
        return True
    except Exception as exc:
        print(f"[!] Failed to write enter-OTA command: {exc}")
        return False


# ?? Phase 2 helper ???????????????????????????????????????????????????????????

async def _run_dfu_transfer(client, hdr: BinHeader, body: bytes, *, verbose: bool) -> bool:
    notif_queue: asyncio.Queue[bytes] = asyncio.Queue()

    def _handler(_sender, data: bytes) -> None:
        notif_queue.put_nowait(bytes(data))

    await client.start_notify(DFU_CONTROL_PT_UUID, _handler)

    packet_size = min(
        MAX_PACKET_SIZE,
        getattr(client, "mtu_size", 23) - 3,
    )

    async def send_cp(data: bytes) -> None:
        await client.write_gatt_char(DFU_CONTROL_PT_UUID, data, response=True)

    async def wait_notif(timeout: float = 10.0) -> bytes:
        return await asyncio.wait_for(notif_queue.get(), timeout=timeout)

    def _check_notif(raw: bytes, expect_op: int, what: str) -> bool:
        """Validate a control-point notification.

        Notifications are [responseType=0x10, requestOpCode, statusCode, ...]
        """
        if len(raw) < 3:
            print(f"[!] {what}: notification too short ({raw.hex()})")
            return False
        if raw[0] != NOTIFY_RESPONSE_CODE:
            # The connection-parameter-update opcode (0x07) generates a
            # notification that DfuService explicitly ignores; skip it.
            if raw[1] == OP_CONNECTION_PARAMETER_UPDATE:
                if verbose:
                    print(f"    (ignored conn-param-update notification: {raw.hex()})")
                return True  # caller should loop for the real notification
            if verbose:
                print(f"    unexpected notification type {raw[0]:#04x}: {raw.hex()}")
            return False
        if raw[1] != expect_op:
            print(f"[!] {what}: expected ack for op {expect_op:#04x}, got {raw[1]:#04x}")
            return False
        status = raw[2]
        if status != DFU_STATUS_SUCCESS:
            name = DFU_STATUS_NAMES.get(status, f"status {status:#04x}")
            print(f"[!] {what}: device returned {name} (raw: {raw.hex()})")
            return False
        return True

    async def send_and_ack(data: bytes, expect_op: int, what: str,
                           timeout: float = 10.0) -> bool:
        await send_cp(data)
        while True:
            raw = await wait_notif(timeout)
            if verbose:
                print(f"    notif ({what}): {raw.hex()}")
            # skip conn-param-update notifications transparently
            if len(raw) >= 2 and raw[1] == OP_CONNECTION_PARAMETER_UPDATE:
                continue
            return _check_notif(raw, expect_op, what)

    try:
        # ?? Step 1: request faster connection parameters ?????????????????
        print("[*] Requesting faster connection parameters…")
        await send_cp(build_conn_param_update())
        # The SDK does NOT wait for a notification here — connection-parameter
        # updates are fire-and-forget.  Drain any stray notification.
        try:
            raw = await asyncio.wait_for(notif_queue.get(), timeout=1.5)
            if verbose:
                print(f"    conn-param notif (ignored): {raw.hex()}")
        except asyncio.TimeoutError:
            pass

        # ?? Step 2: query existing image info (resume support) ???????????
        print("[*] Querying device image info…")
        ok = await send_and_ack(
            build_report_image_info(hdr.signature),
            OP_REPORT_RECEIVED_IMAGE_INFO,
            "ReportImageInfo",
        )
        if not ok:
            return False

        raw_info = notif_queue._queue[0] if not notif_queue.empty() else None  # type: ignore[attr-defined]
        # The real last received notification is already consumed; parse
        # mOriginalVersion and mImageUpdateOffset from it.
        # We need to re-read it — it was popped by wait_notif above.
        # Re-fetch: the notification was already returned by send_and_ack.
        # We parse it from _handler's last delivery via a side channel.
        # Simpler: just track it.
        # We restructure slightly: call wait_notif manually here.

        # Actually, send_and_ack consumed the notification.  We need the
        # raw bytes to extract update_offset.  Refactor: repeat the query
        # manually so we can inspect the payload.
        await send_cp(build_report_image_info(hdr.signature))
        raw_info_notif = await wait_notif(timeout=10.0)
        if verbose:
            print(f"    image-info notif: {raw_info_notif.hex()}")
        if not _check_notif(raw_info_notif, OP_REPORT_RECEIVED_IMAGE_INFO, "ReportImageInfo(2)"):
            return False

        # Parse: [0x10, opcode, status, ver_lo, ver_hi, off0, off1, off2, off3]
        original_version = 0
        update_offset    = 0
        if len(raw_info_notif) >= 9:
            original_version = struct.unpack_from("<H", raw_info_notif, 3)[0]
            update_offset    = struct.unpack_from("<I", raw_info_notif, 5)[0]
        print(
            f"[*] Device image version: 0x{original_version:04x}  "
            f"resume offset: {update_offset}"
        )

        # ?? Step 3: START_DFU ????????????????????????????????????????????
        if update_offset == 0:
            print("[*] Sending START_DFU (full image)…")
            ok = await send_and_ack(
                build_start_dfu(hdr, update_offset),
                OP_START_DFU,
                "StartDfu",
            )
            if not ok:
                return False
        else:
            print(f"[*] Resuming from offset {update_offset} — skipping START_DFU.")

        # ?? Step 4: RECEIVE_FW_IMAGE — arm data channel ??????????????????
        print("[*] Sending RECEIVE_FW_IMAGE…")
        await send_cp(build_receive_fw_image(hdr.signature, update_offset))
        # No notification is expected after RECEIVE_FW_IMAGE in the Java code
        # (it proceeds directly to streaming).

        # ?? Step 5: stream image body ????????????????????????????????????
        start_byte = max(0, update_offset - BIN_HEADER_SIZE)
        total      = len(body)
        offset     = start_byte

        print(f"[*] Streaming {total - start_byte} bytes ({packet_size}B/packet)…")
        while offset < total:
            chunk = body[offset : offset + packet_size]
            await client.write_gatt_char(DFU_DATA_UUID, chunk, response=False)
            offset += len(chunk)
            print(
                f"\r[*] Progress: {100 * offset // total:3d}%  ({offset}/{total})",
                end="", flush=True,
            )
        print()

        # ?? Step 6: VALIDATE_FW_IMAGE ????????????????????????????????????
        print("[*] Validating firmware image…")
        ok = await send_and_ack(
            build_validate(hdr.signature),
            OP_VALIDATE_FW_IMAGE,
            "ValidateFW",
            timeout=30.0,
        )
        if not ok:
            await _send_reset(client, verbose)
            return False

        # ?? Step 7: ACTIVE_IMAGE_RESET — activate + reboot ???????????????
        print("[*] Activating image and rebooting…")
        try:
            await send_cp(bytes([OP_ACTIVE_IMAGE_RESET]))
            # Device reboots immediately; write-response may not arrive.
        except Exception:
            pass

        await client.stop_notify(DFU_CONTROL_PT_UUID)
        print("[?] Realsil DFU complete. Device is rebooting into new firmware.")
        return True

    except asyncio.TimeoutError:
        print("[!] Timed out waiting for device notification.")
        await _send_reset(client, verbose)
        return False
    except Exception as exc:
        print(f"[!] DFU error: {exc}")
        await _send_reset(client, verbose)
        return False
    finally:
        try:
            await client.stop_notify(DFU_CONTROL_PT_UUID)
        except Exception:
            pass


async def _send_reset(client, verbose: bool) -> None:
    """Best-effort: send RESET so the device doesn't stay stuck in DFU mode."""
    try:
        await client.write_gatt_char(
            DFU_CONTROL_PT_UUID, bytes([OP_RESET]), response=True
        )
        if verbose:
            print("    Reset command sent.")
    except Exception:
        pass