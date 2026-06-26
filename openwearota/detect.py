from __future__ import annotations

FAMILY_NAMES = {
    "shb_slb": "SHB/SLB (3rd-gen generic Chinese OTA — YiChip/Ali-BLE)",
    "telink":  "Telink (TLSR8232/TLSR8253/TLSR82xx)",
    "jieli":   "JieLi (AC695N/AC696x — RCSP protocol)",
    "beken":   "Beken (BK3431/BK3432/BK3266)",
    "onmicro": "OnMicro (OM6620 family)",
    "realsil": "RealSil (RTL876x BLE wearable SoC)",
}

SDK_NOTES = {
    "shb_slb": (
        "Most common protocol in $4-$10 watches. Compile firmware with the "
        "YiChip/Ali-BLE 3rd-gen SDK matching your product ID."
    ),
    "telink": (
        "Well-documented chip family. Use the Telink TLSR8xxx IoT SDK. "
        "Monotonic versioning may be enforced — check status code 19."
    ),
    "jieli": (
        "AC695N/AC696x — common in talking/calling smartwatches. Use JieLi's "
        "JL_OTA_SDK for the 695X watch variant."
    ),
    "beken": (
        "BK3431/BK3432/BK3266 — common in older/cheaper bands. Use Beken's "
        "SDK; firmware starts with a 32-byte self-describing header."
    ),
    "onmicro": (
        "OM6620 — less common. OTA support is partial (detect + best-effort "
        "transfer). Capture a real FitPro session with Wireshark for full "
        "ACK/retry semantics before relying on this path."
    ),
    "realsil": (
        "RTL876x — more common in TWS earbuds; occasionally in watches. "
        "OpenWearOTA detects this chip but does NOT yet implement the DFU "
        "transfer flow. See DOCUMENTATION.md §6."
    ),
}


def detect_chip_family(discovered_service_uuids: set[str]) -> str | None:
    u = {x.lower() for x in discovered_service_uuids}
    has_jieli        = "0000ae00-0000-1000-8000-00805f9b34fb" in u
    has_beken        = "f000ffc0-0451-4000-b000-000000000000" in u
    has_onmicro_ota  = "00001234-0000-1000-8000-00805f9b34fb" in u
    has_onmicro_isp  = "6e40ff01-b5a3-f393-e0a9-e50e24dcca9e" in u
    has_telink_ota   = "00010203-0405-0607-0809-0a0b0c0d1912" in u
    has_realsil_dfu  = "00006287-3c17-d293-8e48-14fe2e4da212" in u
    has_shb          = "5833ff01-9b8b-5191-6142-22a4536ef123" in u
    has_slb          = "0000feb3-0000-1000-8000-00805f9b34fb" in u
    if has_realsil_dfu:  return "realsil"
    if has_telink_ota:   return "telink"
    if has_jieli:        return "jieli"
    if has_beken:        return "beken"
    if has_onmicro_ota or has_onmicro_isp: return "onmicro"
    if has_shb or has_slb: return "shb_slb"
    return None


SLB_UUIDS = {
    "fed7": "0000fed7-0000-1000-8000-00805f9b34fb",
    "fed8": "0000fed8-0000-1000-8000-00805f9b34fb",
    "fed5": "0000fed5-0000-1000-8000-00805f9b34fb",
}
SHB_UUIDS = {
    "ff03": "5833ff03-9b8b-5191-6142-22a4536ef123",
    "ff02": "5833ff02-9b8b-5191-6142-22a4536ef123",
    "ff04": "5833ff04-9b8b-5191-6142-22a4536ef123",
}


def detect_shb_slb_mode(characteristic_uuids: set[str]) -> str:
    u = {x.lower() for x in characteristic_uuids}
    flags_write = 0
    flags_shb   = 0
    if SLB_UUIDS["fed7"] in u: flags_write |= 1
    if SLB_UUIDS["fed8"] in u: flags_write |= 2
    if SLB_UUIDS["fed5"] in u: flags_write |= 4
    if SHB_UUIDS["ff03"] in u: flags_shb   |= 1
    if SHB_UUIDS["ff02"] in u: flags_shb   |= 2
    if SHB_UUIDS["ff04"] in u: flags_shb   |= 4
    if flags_write == 7 and flags_shb == 0: return "SLB_UPGRADE"
    if flags_write == 0 and flags_shb == 3: return "SHB_APP"
    if flags_write == 0 and flags_shb == 7: return "SHB_OTA"
    return "UNKNOWN"