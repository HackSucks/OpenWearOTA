# OpenWearOTA — Protocol Reference

This document is the engineering reference for OpenWearOTA. It describes, chip family
by chip family, exactly how the official "FitPro" Android app talks to the SoCs found
inside cheap ($4–$15) Bluetooth LE smart bands and watches, reverse engineered from a
JADX decompile of the FitPro APK (package `com.*`, third‑party vendor SDKs bundled
inside it).

Everything here was reconstructed by reading the actual decompiled Java — GATT UUIDs,
opcode tables, packet layouts, and state machines — not from public vendor docs (which,
for most of these chips, do not exist in English). Treat byte‑for‑byte details as
**reverse engineered and best‑effort**; chip revisions vary and some fields (especially
checksums/CRCs) were transcribed faithfully from obfuscated code and deserve a logic
analyzer / `nRF Connect` capture to confirm against your specific watch before trusting
a write that could brick it.

## Why there are seven different protocols

A "$4 fake watch" is not one product — it is a reference design. A factory buys
whichever BLE SoC is cheapest that week, flashes generic firmware, and ships it under
fifty different brand names. FitPro (and its white‑label clones — same APK, different
splash screen) bundles an OTA client for *every* SoC vendor it has ever shipped with,
and at connect time it fingerprints the watch by GATT service UUID to decide which
client to use. OpenWearOTA does the same thing.

| # | Family (as named by this doc) | Real silicon | GATT fingerprint | Typical use |
|---|---|---|---|---|
| **1** | **SHB/SLB** ("YiChip"/Ali‑BLE 3rd‑gen) | Related to PhyPlus PHY62xx (see §1 and §4 — SHB GATT UUIDs are shared; SLB silicon unconfirmed) | Service `5833FF01‑...` (SHB) or `0000FEB3‑...` (SLB) | The single most common protocol in $4–$10 watches/bands |
| **2** | **Telink** | TLSR8232/TLSR8253/TLSR82xx ("Telink Semiconductor") | Service `00010203-0405-0607-0809-0a0b0c0d1912` | Very common in budget earbuds and watches |
| **3** | **PhyPlus** | PHY6222/PHY6252/PHY62xx ("PhyPlus Microelectronics") | Service `5833FF01‑...` (same as SHB above; distinguished by sub-mode) | Confirmed directly from vendor SDK — most reliable entry in this doc |
| **4** | **JieLi (RCSP)** | AC695N/AC696x ("JieLi"/Zhuhai Jieli) — `JL_CHIP_FLAG_695X_WATCH` | Service `0000ae00-0000-1000-8000-00805F9B34FB` | Common in talking/calling smartwatches |
| **5** | **Beken** | BK3431/BK3432/BK3266 | Service `f000ffc0-0451-4000-b000-000000000000` | Common in older/cheaper bands |
| **6** | **OnMicro** | OM6620 family | Service `00001234-0000-1000-8000-00805f9b34fb` (OTA) / `6e40ff01-...` (ISP/bootloader) | Less common, seen in some bands |
| **7** | **RealSil (RTK)** | RTL876x BLE audio/wearable SoC | Service `0000d0ff-3c17-d293-8e48-14fe2e4da212` | More common in TWS earbuds, occasionally watches |

> **Note on §1 vs §4 (SHB/SLB and PhyPlus):** The SHB GATT service UUID
> (`5833FF01‑...`) and its three characteristics (`FF02`/`FF03`/`FF04`) are
> shared between the older FitPro SHB/SLB OTA protocol and PhyPlus's own
> reference OTA profile as shipped in PHY62XX_SDK 3.x.  They are *related* —
> the same hardware family underlies both — but the opcode generations differ
> enough that they warrant separate drivers and documentation sections.
> OpenWearOTA distinguishes them at runtime by checking which characteristics
> are present (see `detect_shb_slb_mode` in `detect.py`): all three present
> simultaneously (FF02 + FF03 + FF04) → PhyPlus §4 driver; FF03‑only or
> FF02+FF03 → SHB app‑mode §1.

OpenWearOTA's auto‑detect logic (see `openwearota/detect.py`) is exactly the same as
FitPro's: connect, discover services, compare the discovered service UUIDs against the
fingerprint table above, then hand off to the matching driver.

---

## 1. SHB/SLB protocol ("3rd‑gen" generic Chinese OTA)

> **Relationship to PhyPlus (§4):** The SHB GATT service (`5833FF01‑...`) and its
> characteristics are shared with PhyPlus PHY62xx hardware.  When a watch presents all
> three SHB characteristics simultaneously (Command FF02 + Response FF03 + Data FF04),
> OpenWearOTA routes to the PhyPlus driver (§4) rather than this one.  The SLB path
> (`0000FEB3‑...`) is unrelated to PhyPlus and handled entirely in this section.

This is the protocol implemented by FitPro's own `com.ota.otalib` package
(`OTACore.java`). It is actually **two** closely related protocols that share opcodes,
framing style, and even file‑extension based dispatch — "SHB" (an older, partition/HEX
based flashing protocol) and "SLB" (a newer, single flat `.bin`‑based protocol). FitPro
picks one or the other by sniffing the GATT services exposed by the watch, and *also* by
the extension of the firmware file you select. The same opcode space (`0x20`/`0x21`/
`0x22`.../`0x2F`) is reused by a closely related Alibaba/AliOS protocol seen in
`example/otalib/boads/Constant.java` (`ALI_INSTRUCT_*`), confirming this is a known
"family" of OTA protocol used across several Chinese BLE MCU brands, not a single
vendor's bespoke invention.

### 1.1 GATT fingerprint

| Role | UUID |
|---|---|
| SLB service | `0000FEB3-0000-1000-8000-00805F9B34FB` |
| SLB write (with response) | `0000FED5-0000-1000-8000-00805F9B34FB` |
| SLB write (no response, bulk data) | `0000FED7-0000-1000-8000-00805F9B34FB` |
| SLB notify | `0000FED8-0000-1000-8000-00805F9B34FB` |
| SHB service | `5833FF01-9B8B-5191-6142-22A4536EF123` |
| SHB write (with response) | `5833FF02-9B8B-5191-6142-22A4536EF123` |
| SHB notify | `5833FF03-9B8B-5191-6142-22A4536EF123` |
| SHB write (no response, bulk data) | `5833FF04-9B8B-5191-6142-22A4536EF123` |
| CCCD (standard) | `00002902-0000-1000-8000-00805F9B34FB` |

A watch in this family will expose **either** the SLB service **or** the SHB service —
never both at the same time — though a watch may switch from a "normal" advertising mode
into one of these two OTA modes only after being sent a special "enter OTA" command over
its *normal* characteristic (see §1.5, "App‑mode handover"). This is why FitPro's scan
logic does a two‑stage scan: connect once in normal mode, write a magic command, watch
disconnects and starts re‑advertising under a *different, incremented* MAC address, and
the app has to find it again.

### 1.2 Detecting which sub‑protocol (SHB vs SLB) and which mode

After `discoverServices()`, FitPro inspects the union of service UUIDs and the specific
characteristics inside whichever OTA service is present:

```
if no SLB and no SHB service present       -> not an OTA-capable device right now
if SLB service present:
    inspect characteristics of that service for OTA "mode" sub-type (see table below)
if SHB service present:
    same, but against the SHB characteristic set
```

Within whichever service is present, the exact combination of characteristics found
disambiguates the *mode* the chip is currently in:

| Characteristics present | Mode |
|---|---|
| SHB notify (`...FF03`) only | **SBH "App" mode** — chip is running normal firmware, exposing only a thin command channel used to ask it to reboot into OTA mode |
| SHB notify + SHB write‑no‑rsp (`...FF03` + `...FF04`) | **SBH "OTA" mode** — chip rebooted into its OTA‑capable firmware/bootloader, ready to receive a HEX/RES image |
| SLB write‑no‑rsp only (`...FED7`) | **SLB upgrade mode** — chip ready to receive a flat `.bin` |

In code terms (this is a direct port of `BleUtils.getOTATypeForCharacteristic`):

```python
flags_write = 0  # bit0=FED7(SLB no-rsp) bit1=FED8(SLB notify) bit2=FED5(SLB w/rsp)
flags_shb   = 0  # bit0=FF03(notify) bit1=FF02(w/rsp) bit2=FF04(no-rsp)
for char in characteristics:
    {"0000FED7-...": lambda: flags_write.__ior__(1),
     "0000FED8-...": lambda: flags_write.__ior__(2),
     "0000FED5-...": lambda: flags_write.__ior__(4),
     "5833FF03-...": lambda: flags_shb.__ior__(1),
     "5833FF02-...": lambda: flags_shb.__ior__(2),
     "5833FF04-...": lambda: flags_shb.__ior__(4)}[char.uuid]()

if flags_write == 7 and flags_shb == 0:   mode = "SLB_UPGRADE"
elif flags_write == 0 and flags_shb == 3: mode = "SHB_APP"
elif flags_write == 0 and flags_shb == 7: mode = "SHB_OTA"
else: mode = "ERROR"
```

If the mode is `SLB_UPGRADE` or `SHB_OTA`, FitPro immediately calls
`requestMtu(517)` before doing anything else — both sub‑protocols want the largest MTU
the phone/chip will negotiate, since payload size directly drives transfer speed.

### 1.3 Generic packet envelope (used by **both** SHB and SLB, slightly differently)

Both protocols frame every command the same way at the lowest level — a small header
followed by up to `MTU‑7` bytes of payload:

```
byte 0 : message sequence number (0–15, wraps, low nibble only)
byte 1 : opcode
byte 2 : high nibble = (totalPackets - 1) & 0xF,  low nibble = packetIndex & 0xF
byte 3 : payload length in this packet (0–MTU-7)
byte 4..: payload bytes
```

This is built by `OTACore.generateSlbData()`:

```python
def generate_slb_frame(seq, opcode, payload, total_packets, packet_index, mtu):
    n = min(len(payload), mtu - 7)
    frame = bytearray(4 + n)
    frame[0] = seq & 0x0F
    frame[1] = opcode
    frame[2] = (((total_packets - 1) & 0x0F) << 4) | (packet_index & 0x0F)
    frame[3] = n
    frame[4:4+n] = payload[:n]
    return bytes(frame)
```

The SHB side uses a **simpler hex‑string command form** without the 4‑byte SLB header for
most control commands (e.g. `"0102"`, `"21" + productID + bootVersion`), and only uses
the length‑prefixed framing for actual firmware bytes inside a block.

### 1.4 SLB opcode table

| Opcode (dec) | Hex | Direction | Name | Notes |
|---|---|---|---|---|
| 32 | 0x20 | App→Watch | `REQUEST_DEVICE_FIRMWARE_VERSION` | payload `{0x00}` |
| 33 | 0x21 | Watch→App | `RESPONSE_DEVICE_FIRMWARE_VERSION` | |
| 34 | 0x22 | App→Watch | `REQUEST_SENDS_AN_UPGRADE_REQUEST` | carries productID/CRC/length, see §1.6 |
| 35 | 0x23 | Watch→App | `RESPONSE_TO_UPGRADE_REQUEST` | byte0 = 1 means "accepted"; also carries packet‑size nibble |
| 36 | 0x24 | Watch→App | `RESPONSE_RECEIVE_DATA_CHECK` | watch tells app which file offset it actually wants next (flow control / resend) |
| 37 | 0x25 | App→Watch | `NOTIFY_FIRMWARE_SEND_COMPLETE` | payload `{0x01}` |
| 38 | 0x26 | Watch→App | `RESPONSE_FIRMWARE_VERIFICATION_RESULTS` | byte0==1 → success |
| 39–44 | 0x27–0x2C | both | `SLB_ENC_1`..`SLB_ENC_6` | AES‑128 ECB challenge/response, see §1.7 |
| 47 | 0x2F | App→Watch | `SEND_FIRMWARE_PACKET_DATA` | the actual firmware bytes, sent via the *no‑response* characteristic |

### 1.5 SLB upgrade flow (flat `.bin`, the common case)

1. App connects, discovers services, finds `SLB_UPGRADE` mode, requests MTU 517.
2. App enables notifications on `0000FED8`.
3. App sends opcode `0x20` (get firmware version) — `generate_slb_frame(seq, 0x20, b'\x00', 1, 0)`.
4. Watch replies opcode `0x23` indirectly via the "upgrade request" handshake (FitPro's
   code treats the *first* `0x23` notification specially: if the raw frame is exactly
   9 bytes it's read as a bare version reply; if ≥13 bytes it's read as a product‑ID +
   boot‑version reply and is checked against the firmware file's embedded product ID —
   **the app refuses to continue if they don't match**, which is the chip's own anti‑brick
   guard rail).
5. App computes CRC‑16/CCITT‑FALSE (poly `0x1021`, init `0xFFFF`, see §1.8) over the
   entire firmware payload, builds the "upgrade request" packet (opcode `0x22`):

   ```
   payload (12 or 16 bytes depending on whether a "resource config address" is present):
     byte 0       : 0x00 = "use embedded version field", 0x01 = "use resConfigAddress"
     byte 1..4    : version bytes OR resConfigAddress (little-endian)
     byte 5..8    : firmware length, little-endian uint32
     byte 9..10   : CRC16 over firmware, little-endian
     byte 11      : 0x00
   ```

6. Watch replies opcode `0x23`. Byte 0 of the *decoded* payload is the "accept" flag —
   if it isn't `1`, the watch is refusing the upgrade (usually because the firmware's
   embedded product ID doesn't match the chip) and the connection should be torn down.
   The low nibble of byte 5 + 1 is the **packets‑per‑burst** the watch is willing to
   accept before it must ACK (this becomes "packet size" in the rest of the flow).
7. App starts streaming firmware. Each burst is `packet_size` frames of up to `MTU‑7`
   bytes of raw firmware, opcode `0x2F` (`SEND_FIRMWARE_PACKET_DATA`), sent on the
   **write‑without‑response** characteristic (`0000FED7`) back‑to‑back, then the app
   waits for opcode `0x24` from the watch.
8. Opcode `0x24` (`RESPONSE_RECEIVE_DATA_CHECK`) tells the app the **byte offset** the
   watch has actually buffered (4‑byte little‑endian, payload bytes 1‑4). If that offset
   doesn't match what the app expected, the app recomputes which `packet_size`‑sized
   burst to (re)send from that offset — this is the protocol's loss‑recovery mechanism;
   there is no per‑packet ACK, only per‑burst position confirmation.
9. When the offset reported equals the total firmware length, the app sends opcode
   `0x25` (`NOTIFY_FIRMWARE_SEND_COMPLETE`, payload `{0x01}`).
10. Watch replies opcode `0x26` (`RESPONSE_FIRMWARE_VERIFICATION_RESULTS`). Byte 0 == 1
    means the watch verified its own checksum/CRC of the now‑fully‑written image and
    accepted it — the watch will now reboot on its own. Anything else is a failure and
    the connection should be dropped (retrying from scratch on reconnect).

### 1.6 SLB "resource config address" variant

Some firmware files are not raw application binaries but **resource packs** (fonts,
images, watch faces). These use a different upgrade‑request payload that points at a
fixed flash offset (`slbResConfigAddress`) instead of embedding the version — this
address, if present, is parsed straight out of the firmware filename
(`res_<8 hex chars>.bin` or `RES_<8 hex chars>.bin`). OpenWearOTA's `SLBFile` parser
replicates this filename convention.

### 1.7 SLB AES "secure OTA" variant (`hexe16.bin`)

Firmware files whose name ends in `hexe16.bin` trigger a 3‑step AES‑128/ECB
challenge‑response *before* the normal upgrade flow in §1.5 begins, using opcodes
39/40/41/42/43/44 (`0x27..0x2C`):

1. App generates a random 16‑byte string (`randomStr`), AES‑encrypts it with the shared
   key (`mKey`, provisioned out‑of‑band / hardcoded per‑product), sends as opcode `0x27`.
2. Watch replies opcode `0x28` with its own encrypted blob; app stores this as
   `firmwareData` (still encrypted) and replies with opcode `0x29` carrying the
   plaintext `randomStr` it generated in step 1, AES‑encrypted.
3. Watch replies opcode `0x2A`. App decrypts the blob it stored in step 2 using `mKey`
   and compares it byte‑for‑byte against the plaintext from the watch's `0x2A` reply.
   If they match, app re‑encrypts (encrypt(encrypt(plaintext, randomStr), mKey)) and
   sends as opcode `0x2B`.
4. Watch replies opcode `0x2C` — at this point the key exchange is considered complete
   and the app proceeds to the *normal* §1.5 flow (calling `startSLBOTA` again, this
   time without re‑triggering the security handshake).

This is a real, working anti‑clone DRM scheme — **OpenWearOTA can only complete this
flow if you know the per‑product AES key.** Without it, secure firmware images cannot
be flashed; this is a hard vendor‑side restriction, not a bug. Treat any
`*hexe16*` firmware as out of scope unless you've sourced the key from the original
vendor SDK/tooling.

### 1.8 SHB partition‑based flow — older generation (`.hex` / `.hex4` / `.hex16` / `.res` / `.hexe16`)


The SHB side is older and partition‑oriented — closer to "this is a list of flash
regions, write each one in 16‑line blocks" than a flat binary stream. The hex command
strings below are sent as literal ASCII‑hex (e.g. `"0102"` means the two raw bytes
`0x01 0x02`) over the write‑with‑response characteristic; the watch's notify replies use
the same convention.

**App‑mode handover (before OTA can start):**

| Sent | Meaning |
|---|---|
| `0103` | enter OTA mode for a `.res` (resource) file |
| `0102` | enter OTA mode for a `.hex`/`.hex4`/`.hex16` file |
| `05` + AES(`randomStr`) | enter OTA mode for a secure `.hexe16` file (same AES scheme as §1.7, mirrored opcodes `0x05`–`0x08`/`0x71`–`0x73`/`0x8B`–`0x8D`) |

Sending `0102`/`0103` makes the watch **disconnect and re‑advertise** in OTA mode
(usually with the MAC's last octet incremented by one — see `BleUtils.compareMac`), so
the app must re‑scan for it.

**Once reconnected in SHB‑OTA mode**, the flow is partition‑oriented:

| Sent / Received | Direction | Meaning |
|---|---|---|
| `01` + partitionCount(1B) + `00` | →watch | "here is how many partitions follow" |
| `0081` | ←watch | "ready, send partition info for partition 0" |
| `04` + index + flashAddr(4B,BE‑swapped) + partitionAddr(4B) + length(4B) + checksum(2B) | →watch | per‑partition header (`makePartitionCmd`) |
| `0083` | ←watch | "partition header accepted, send first block" |
| `0084` | ←watch | "send next block of current partition" |
| (raw hex string, one "block" = up to 16 lines of `MTU‑3` bytes each) | →watch | firmware block, sent on the *no‑response* characteristic |
| `0085` | ←watch | "partition complete, send next partition's header" (loops back to the `04`+... step, or finishes if this was the last partition) |
| `0087` | ←watch | "send next block within the current partition" (mid‑partition continuation) |
| `0089` | ←watch | "resend current partition's header" (recoverable error) |
| `0091` or `FF` | ←watch | "abort/retry from the very beginning" (re‑enters app‑mode handover) |
| `0591` / `0901` / `2B91` | ←watch | version‑check failure — disconnect |
| `6887` | ←watch | generic error — disconnect |
| `02` + flashAddr16(4B) + bootVersion(4B) | →watch | sent for `.res` resource files instead of the partition loop |
| `0004` (literal `_3GenOtaDataInteraction.UPDATE_FLASH_MODE_FONT`‑style constant, value `"04"`) | both | final "flash complete" handshake — when the watch echoes this back, OTA succeeded |

The partition checksum used in the `04` header is a 16‑bit CRC with polynomial
`0xA001` (reflected `0x8005`), computed by `BleUtils.getPartitionCheckSum` — a classic
CRC‑16/ARC variant (see §1.9 for the implementation — it's shared with §1.8b below).

Firmware blocks within a partition are chunked into "lines" of `MTU‑3` bytes
(`Partition.analyzePartition`), 16 lines per "block", and each block is preceded by an
implicit `0x84` ready‑signal from the watch — i.e. the protocol is fundamentally
*watch‑paced*: the app never blasts data; it always waits for the watch's next request
before sending the next chunk.


### 1.9 CRC‑16 (firmware‑level, SLB flow)

Used in the `0x22` upgrade‑request payload (see §1.5 step 5). This is the standard
CRC‑16/XMODEM (a.k.a CRC‑16‑CCITT, init `0xFFFF`, poly `0x1021`, no reflection):

```python
def crc16_xmodem(data: bytes, crc: int = 0xFFFF) -> int:
    for b in data:
        crc ^= (b << 8)
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if (crc & 0x8000) else (crc << 1)
        crc &= 0xFFFF
    return crc
```

### 1.10 Identifying chip/product from a connected watch

Once you are in `SLB_UPGRADE` mode, sending opcode `0x20` and reading back the `0x23`
notification's payload (if ≥9 bytes) gives you a 4‑byte version directly; if the watch
also reports a "first upgrade request" reply ≥13 bytes you additionally get a 2‑byte
product ID and a 6‑byte boot version, both of which OpenWearOTA surfaces to the user as
"Product ID: XXXX  Boot version: YYYYYY" — this is the closest thing this protocol has
to a chip/series identifier visible over the air, and it is also embedded in the
firmware file itself (so the watch can refuse mismatched firmware, and so can we, before
even attempting a write).

---

## 2. Telink protocol (TLSR8xxx)

Telink's OTA scheme is the simplest of the five, and is a fairly well‑known, often
publicly documented (by Telink themselves, in SDK PDFs that circulate widely) BLE OTA
mechanism. FitPro's `com.telink.ota` package is a thin GATT command‑queue wrapper
(`Peripheral.java`) around it; the packet format itself lives in `OtaPacketParser.java`.

### 2.1 GATT fingerprint

| Role | UUID |
|---|---|
| OTA service | `00010203-0405-0607-0809-0a0b0c0d1912` |
| OTA characteristic (write + notify) | `00010203-0405-0607-0809-0a0b0c0d2b12` |
| Version service (optional, informational) | `0000d0ff-3c17-d293-8e48-14fe2e4da212` |
| Version characteristic | `0000ffd4-0000-1000-8000-00805f9b34fb` |
| Battery service (standard) | `0000180f-0000-1000-8000-00805f9b34fb` |

Note the version service UUID (`0000d0ff-3c17-...`) is the **same base UUID** RealSil
uses for its own OTA service — both vendors' SDKs were clearly derived from a common
Telink‑adjacent reference design lineage; don't let that coincidence cause a
misdetection. Disambiguate using the characteristic UUIDs, not just the service UUID,
when both `00010203-...-1912` (Telink) and `0000d0ff-...` (RealSil) appear to be
present — only Telink chips will also expose `...2b12`.

### 2.2 Packet format

Every OTA packet is exactly **20 bytes**, written to the single OTA characteristic
using write‑without‑response:

```
byte 0-1  : packet index, little-endian uint16 (0-based)
byte 2-17 : up to 16 bytes of firmware payload (pad with 0xFF if this is the last,
            short packet)
byte 18-19: CRC16, little-endian, computed over bytes 0-17 of *this same packet*
            (i.e. index bytes + payload bytes, NOT a running CRC over the whole file)
```

The **very last packet** of the transfer is special: its 2‑byte index field is
`0xFFFF`, and its 16 payload bytes are all `0xFF` — this is the "end of OTA" sentinel,
not real firmware data, and tells the chip to finalize and reboot.

```python
def telink_crc16(buf16: bytes) -> int:
    # buf16 is the 18 bytes [index_lo, index_hi, payload(16)]
    table = (0x0000, -24575 & 0xFFFF)
    crc = 0xFFFF
    for byte in buf16:
        b = byte
        for _ in range(8):
            crc = (table[(crc ^ b) & 1] ^ (crc >> 1)) & 0xFFFF
            b >>= 1
    return crc

def build_telink_packet(index: int, chunk: bytes) -> bytes:
    pkt = bytearray([0xFF] * 20)
    pkt[0] = index & 0xFF
    pkt[1] = (index >> 8) & 0xFF
    pkt[2:2 + len(chunk)] = chunk
    crc = telink_crc16(bytes(pkt[0:18]))
    pkt[18] = crc & 0xFF
    pkt[19] = (crc >> 8) & 0xFF
    return bytes(pkt)

def build_telink_end_packet(next_index: int) -> bytes:
    pkt = bytearray([0xFF] * 16)
    idx = bytearray(2)
    idx[0] = next_index & 0xFF
    idx[1] = (next_index >> 8) & 0xFF
    body = bytes(idx) + bytes(pkt)
    crc = telink_crc16(body)
    return body + bytes([crc & 0xFF, (crc >> 8) & 0xFF])
```

The firmware file is split into 16‑byte chunks; total packet count is
`ceil(len(firmware) / 16)`. The first 6 bytes of the firmware image (bytes 2‑5,
specifically) are conventionally the firmware's own embedded version, and Telink's SDK
exposes `getFirmwareVersion()` purely for the app's own sanity‑check UI — it is **not**
sent to the chip as part of the handshake; Telink OTA has no separate "version check"
exchange at the protocol level the way SLB/JieLi do.

### 2.3 Flow

1. Connect, discover services, request a larger MTU if supported (helps throughput but
   Telink's OTA characteristic write is fixed at 20 bytes per packet regardless — MTU
   mostly helps by letting more packets fit per BLE connection event when using
   write‑without‑response back‑to‑back).
2. Enable notifications on the OTA characteristic (`...2b12`) — the chip uses this same
   characteristic to send back a **1‑byte status code** at the end of the transfer (see
   §2.4) and, on some firmware builds, periodically during transfer.
3. Stream packets in order, index `0` upward, pacing writes so as not to overwhelm the
   chip's internal flash‑write buffer — Telink's own reference app uses roughly an 8 ms
   delay between packets by default (`OtaSetting.readInterval`); OpenWearOTA exposes
   this as a configurable `--pace-ms` flag and defaults conservatively.
4. After the final real data packet, send the special "end" packet with index =
   `total_packets` (i.e. one past the last real index) and an all‑`0xFF` payload, per
   `getCheckPacket()`.
5. Wait (default timeout 300000 ms / 5 minutes — Telink chips can be slow to erase/write
   internal flash) for a notification on the OTA characteristic containing a single
   status byte.

### 2.4 Status codes

| Value | Meaning |
|---|---|
| 0 | Success |
| 1 | Started |
| 2 | Stopped |
| 4 | Busy |
| 5 | Rebooting |
| 16 | Fail: bad parameters |
| 17 | Fail: connection interrupted |
| 18 | Fail: battery check failed (chip refuses OTA below some battery threshold — charge the watch first) |
| 19 | Fail: version compare error (chip thinks the new firmware isn't newer — some Telink builds enforce monotonic versioning) |
| 20 | Fail: packet sent error |
| 21 | Fail: packet sent timeout |
| 22 | Fail: flow timeout |
| 23 | Fail: reconnect error |
| 24 | Fail: device not connected |
| 25 | Fail: service not found |
| 26 | Fail: characteristic not found |

Any code ≥16 is a failure (`StatusCode.isFailed()`); the chip will *not* apply the new
firmware and will keep running its old image — this is a fail‑safe by design and is one
of the friendlier protocols to experiment against for that reason.

---

## 3. PhyPlus protocol (PHY62xx — SDK‑confirmed)

> **This is the most reliable section in the document.** Unlike every other
> protocol here, this one is transcribed directly from PhyPlus's own
> `PHY62XX_SDK_3.1.1` (`components/profiles/ota/ota_service.c` +
> `ota_protocol.c`), which PhyPlus distributes publicly (GitHub mirror and
> PhyPlus download portal) without restriction.  No reverse‑engineering was
> needed — just reading vendor source.

**Relationship to §1 (SHB/SLB):** The SHB GATT service UUID
(`5833FF01‑9B8B‑5191‑6142‑22A4536EF123`) and its three characteristics
(`FF02`/`FF03`/`FF04`) are shared between FitPro's older SHB/SLB OTA
protocol (§1) and PhyPlus's own reference OTA profile.  The two are related —
the same hardware family (PHY62xx) underlies the SHB side of §1 — but the
opcode generations differ:

- **SHB / SDK 2.x generation (§1.8):** opcodes recovered by JADX decompile of
  FitPro's `com.ota.otalib`; targets older PHY6212 / SDK 2.x firmware.
- **PhyPlus / SDK 3.x generation (this section):** opcodes from vendor source;
  targets PHY6222/PHY6252/PHY62xx family with SDK 3.1.1 firmware.

Both use the same GATT fingerprint.  OpenWearOTA disambiguates at runtime via
`detect_shb_slb_mode()`: if all three characteristics (FF02 + FF03 + FF04) are
present simultaneously, this driver is used; FF03‑only or FF02+FF03 indicates
SHB app‑mode (§1).

### 3.1 GATT fingerprint

| Role | UUID |
|---|---|
| Service | `5833FF01-9B8B-5191-6142-22A4536EF123` |
| Command (write, with response) | `5833FF02-9B8B-5191-6142-22A4536EF123` |
| Response (notify) | `5833FF03-9B8B-5191-6142-22A4536EF123` |
| Data (write, no response) | `5833FF04-9B8B-5191-6142-22A4536EF123` |

Enabling notifications on **Response** is the action that flips the device
from "connected" to "ready for OTA commands" in the SDK's internal state
machine — this must happen *before* sending `START_OTA`, not just before
waiting for its reply.

### 3.2 Command opcodes (written to Command characteristic)

| Opcode | Name | Payload |
|---|---|---|
| `0x01` | `START_OTA` | `sectorCount(1B)` + `burstSize(1B)` — `burstSize=0` defaults to 16; `0xFF` means unbounded |
| `0x02` | `PARTITION_INFO` | `index(1B)` + `flashAddr(4B, LE)` + `runAddr(4B, LE)` + `size(4B, LE)` + `checksum(2B, LE)` |
| `0x04` | `REBOOT` | empty for immediate reset; `0x01` to arm graceful reboot-on-disconnect |
| `0x05` | `ERASE` | `flashAddr(4B, LE)` + `size(4B, LE)` — only valid in `WAIT_PARTITION_INFO` state on resource-type OTA |

A security‑boot variant (`SEC_CONFIRM`/`RND_CHANGE`/`VERIFY_KEY`, AES‑128
challenge–response over a per‑device key) exists in the same opcode space but
is not implemented — it requires a per‑product key OpenWearOTA has no way to
obtain, identical to the `.hexe16` limitation in §1.7.

### 3.3 Response opcodes (received as notifications on Response)

| Opcode | Name | Meaning |
|---|---|---|
| `0x81` | `RSP_START_OTA` | ack for `START_OTA` |
| `0x84` | `RSP_PARTITION_INFO` | partition header accepted; ready for data |
| `0x87` | `RSP_BLOCK_BURST` | flow‑control checkpoint; client may send another burst |
| `0x89` | `RSP_ERASE` | ack for `ERASE` |
| `0x8A` | `RSP_REBOOT` | ack for `REBOOT` |
| `0xFF` | `RSP_ERROR` | failure — first payload byte is a numeric error code |

Every response notification's payload is `[errorOrFirstByte, ..., opcodeByte]`
— the SDK always appends the responding opcode as the last byte, with any
associated data in between.

**Error codes:**

| Code | Meaning |
|---|---|
| 100 | invalid OTA state |
| 101 | bad data size |
| 102 | CRC mismatch |
| 103 | no application data |
| 104 | bad application data |
| 105 | unknown command |
| 106 | crypto verify failed |
| 107 | security key verify failed |
| 108 | double-confirm security failure |
| 109 | MIC checksum mismatch |

### 3.4 Flow (single‑partition, the common case)

1. Enable notifications on **Response** (CCCD write) — this alone flips the
   device into "connected, ready for commands."
2. Send `START_OTA` with `sectorCount=1`, `burstSize=16`.  Wait for
   `RSP_START_OTA`.
3. Send `PARTITION_INFO` for partition 0 — `flashAddr`/`runAddr` are normally
   equal (execute‑in‑place); `size` is the firmware byte count; `checksum` is
   CRC‑16/ARC over the entire firmware blob (see §3.5).  Wait for
   `RSP_PARTITION_INFO`.
4. Stream firmware to **Data** in chunks of `negotiatedMTU − 3` bytes.  After
   every `(mtu‑3) × burstSize` bytes, wait for `RSP_BLOCK_BURST` before
   continuing — this is the only flow‑control mechanism; there is no per‑packet
   ack.
5. When all bytes have been sent the device CRC‑checks internally, programs
   flash, and the OTA is complete.  No separate "commit" command.  It either
   succeeds silently or sends `RSP_ERROR`.
6. Device reboots into new firmware on disconnect, or immediately if `REBOOT`
   is sent.

### 3.5 CRC‑16/ARC (firmware-level checksum)

Same algorithm as §1.9 — confirmed by comparing against
`components/libraries/crc16/crc16.c` in the SDK:

```python
def crc16_arc(data: bytes) -> int:
    crc = 0
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if (crc & 1) else (crc >> 1)
    return crc & 0xFFFF
```

### 3.6 Default flash address

PHY62x2's standard OTA‑area base address is `0x11000000`.  This is the default
used by `drivers/phyplus.py`; pass an explicit `flash_addr` argument if your
firmware targets a different offset.

---

## 4. JieLi protocol (RCSP — "Resource Control Service Protocol")

JieLi (Zhuhai Jieli Technology) ships the AC695N/AC696x family, explicitly flagged for
watches in their own SDK as `JL_CHIP_FLAG_695X_WATCH = 8` — this is the chip family to
expect in any smartwatch that supports **Bluetooth calling** (the AC69xx series has a
built‑in audio codec/amp for the speaker+mic, which talking-smartwatches need). JieLi's
protocol, RCSP, is the most elaborate of the five — a generic, extensible command/response
RPC framework that OTA is just one "service" within.

### 4.1 GATT fingerprint

| Role | UUID |
|---|---|
| Service | `0000ae00-0000-1000-8000-00805F9B34FB` |
| Write | `0000ae01-0000-1000-8000-00805F9B34FB` |
| Notify | `0000ae02-0000-1000-8000-00805F9B34FB` |

JieLi also supports classic Bluetooth SPP as a transport (UUID
`00001101-0000-1000-8000-00805F9B34FB`, the standard SPP UUID) for watches that pair
over Bluetooth Classic for calling — OpenWearOTA's desktop tool focuses on the BLE GATT
transport since that's universally available, but the wire‑level RCSP framing below is
identical regardless of transport.

### 4.2 Packet framing

```
byte 0    : 0xFE  (PREFIX_FLAG_FIRST)
byte 1    : 0xDC  (PREFIX_FLAG_SECOND, stored as signed -36)
byte 2    : 0xBA  (PREFIX_FLAG_THIRD, stored as signed -70)
byte 3-4  : payload length (of everything from byte 5 to the opcode+data, little-endian
            uint16) -- NOT including this header or the trailing 0xEF
byte 5    : [only present if packet "type" bit == 0, i.e. a "response" packet] status byte
byte 5/6  : opcode-sequence-number (sn) -- 1 byte, increments per request, echoed in response
byte 6/7  : [only present if "has extra opcode" bit set] extended opcode byte
next      : opcode (1 byte)
next..    : payload bytes
last byte : 0xEF  (END_FLAG)
```

The "type" and "has‑response" bits referenced above are packed into **byte 0 of the
pre‑header payload**, decoded via `CHexConver.getBooleanArrayBig`: bit 7 is `type`
(0 = this is a *response*; 1 = this is a *request*/notification‑style packet with no
status byte), bit 6 is `hasResponse`. In practice, for the purposes of driving an OTA
session as a client, you mostly care about: send a request frame, opcode in the clear,
4‑byte length header, terminated by `0xEF`; parse responses the same way and check the
status byte.

```python
PREFIX = bytes([0xFE, 0xDC, 0xBA])
END = 0xEF

def build_rcsp_frame(opcode: int, payload: bytes, sn: int) -> bytes:
    # Request frame: type=1 (no status byte expected to be parsed back at this offset),
    # has-response=1 for normal commands.
    inner = bytes([sn, opcode]) + payload
    length = len(inner)
    frame = bytearray()
    frame += PREFIX
    frame += bytes([length & 0xFF, (length >> 8) & 0xFF])
    frame += inner
    frame.append(END)
    return bytes(frame)

def parse_rcsp_frame(buf: bytes):
    assert buf[0:3] == PREFIX
    length = buf[3] | (buf[4] << 8)
    body = buf[5:5 + length]
    assert buf[5 + length] == END
    status = body[0]      # present for response-type frames
    sn = body[1]
    opcode = body[2]
    payload = body[3:]
    return status, sn, opcode, payload
```

(`RcspParser.java`'s real implementation is a streaming/reassembling version of the
above that tolerates partial reads across multiple BLE notifications and multiple
logical packets arriving in one notification — OpenWearOTA's Python implementation
keeps a small receive buffer and replicates that reassembly loop.)

### 4.3 Opcode table (the ones relevant to OTA)

| Opcode (dec) | Hex | Name | Direction / Notes |
|---|---|---|---|
| 1 | 0x01 | `CMD_DATA` | generic data‑channel passthrough |
| 2 | 0x02 | `CMD_GET_TARGET_FEATURE_MAP` | what does this chip support |
| 3 | 0x03 | `CMD_GET_TARGET_INFO` | **chip/product identification** — see §5.5 |
| 6 | 0x06 | `CMD_DISCONNECT_CLASSIC_BLUETOOTH` | |
| 11 | 0x0B | `CMD_SWITCH_DEVICE_REQUEST` | |
| 194/195/196 | 0xC2/C3/C4 | TWS advertising/notify commands | not OTA‑relevant for single‑watch use |
| 209 | 0xD1 | `CMD_SETTINGS_COMMUNICATION_MTU` | negotiate app‑level MTU (separate from BLE‑level MTU) |
| 212 | 0xD4 | `CMD_GET_DEV_MD5` | ask the chip for an MD5 of its *current* firmware (optional, used for verification UIs) |
| 225 | 0xE1 | `CMD_OTA_GET_DEVICE_UPDATE_FILE_INFO_OFFSET` | **resume support** — ask where to continue from |
| 226 | 0xE2 | `CMD_OTA_INQUIRE_DEVICE_IF_CAN_UPDATE` | "can I update you right now" pre‑check |
| 227 | 0xE3 | `CMD_OTA_ENTER_UPDATE_MODE` | must succeed before sending any firmware bytes |
| 228 | 0xE4 | `CMD_OTA_EXIT_UPDATE_MODE` | cleanly leave update mode (used on abort) |
| 229 | 0xE5 | `CMD_OTA_SEND_FIRMWARE_UPDATE_BLOCK` | the actual firmware transfer command |
| 230 | 0xE6 | `CMD_OTA_GET_DEVICE_REFRESH_FIRMWARE_STATUS` | poll whether the chip has finished writing/verifying |
| 231 | 0xE7 | `CMD_REBOOT_DEVICE` | reboot to apply |
| 232 | 0xE8 | `CMD_OTA_NOTIFY_UPDATE_CONTENT_SIZE` | tell the chip the total firmware size up front |
| 240 | 0xF0 | `CMD_CUSTOM` | RCSP escape hatch for vendor‑specific commands, unused by OpenWearOTA |

### 4.4 Flow

```
GetTargetInfo(3)                  -> chip/product/version info, see §5.5
InquireUpdate(226, payload=firmware MD5 or empty)
                                   -> response byte0: 0 = OK to proceed, nonzero = refused
                                      (refused commonly means: wrong product, battery low,
                                       or an update is already in progress)
EnterUpdateMode(227)              -> response byte0: 1 = can update, else refused
NotifyUpdateContentSize(232, payload = total length as 4-byte LE [+ current progress
                                       4-byte LE if resuming])
GetUpdateFileOffset(225)          -> response: 4-byte offset + 2-byte length the chip
                                      wants next (== 0 on a clean/fresh start; nonzero
                                      means the chip remembers a partial previous
                                      transfer and wants you to resume from there)
loop:
    SendFirmwareUpdateBlock(229, payload = 4-byte offset (LE) + firmware bytes for that
                                  offset, sized to the negotiated app-level MTU)
    -> response echoes the offset/length actually written; advance and repeat until the
       whole file has been sent
GetDeviceRefreshFirmwareStatus(230) -> poll until response byte0 indicates "done"
RebootDevice(231, payload = 0x00)   -> apply
ExitUpdateMode(228)                 -> tidy up (also call this on any abort/failure path)
```

The presence of `GetUpdateFileOffset` (225) means **JieLi OTA is naturally resumable** —
if a transfer is interrupted (BLE disconnect, app crash), reconnecting and re‑running
`EnterUpdateMode` → `GetUpdateFileOffset` will tell you exactly where to pick back up,
rather than starting from byte 0. OpenWearOTA's JieLi driver always calls this and
resumes automatically rather than always restarting.

### 4.5 `GetTargetInfo` (opcode 3) — chip/product identification

This is the single richest identification response across all five protocols, and is
what OpenWearOTA's `--detect`/auto‑detect path leans on most heavily once it has
confirmed the JieLi GATT fingerprint. The response (`TargetInfoResponse`) includes,
among other things:

- `name` — human‑readable device name string burned into firmware
- `vid` / `pid` / `uid` — vendor/product/unit IDs
- `projectCode` — JieLi's internal project/SDK codename for this firmware build
- `versionName` / `versionCode` — current firmware version
- `protocolVersion` — RCSP protocol revision the chip speaks
- `ubootVersionName` / `ubootVersionCode` — bootloader version
- `isNeedBootLoader` / `isSupportDoubleBackup` / `isSupportMD5` — capability flags that
  determine exactly which of the flow's optional steps (MD5 check, dual‑bank fallback)
  are available
- `singleBackupOtaWay` — `0`=none, `1`=BLE, `2`=SPP — tells you whether this *specific*
  chip's current firmware build even supports OTA over the transport you're connected
  with right now
- `mandatoryUpgradeFlag` — chip‑side hint that the current firmware considers this
  upgrade non‑optional (cosmetic for our purposes, but surfaced in the CLI output)

OpenWearOTA prints `name`, `projectCode`, `vid:pid`, and `versionName` directly to the
user as the "chip identification" line for JieLi devices — this is the closest analog to
"chip series/family" JieLi's protocol exposes over the air, short of a full register
dump.

---

## 5. Beken protocol (BK3431/BK3432/BK3266)

Beken's own SDK (`com.beken.beken_ota`) is the most "raw" of the five — a flat,
fixed‑header command protocol with no encryption, no resumability, and a very small
opcode space. It also, notably, supports **both BLE and classic‑Bluetooth SPP**
transports with the *exact same* command bytes (only the underlying `sendDataToDevice`
transport differs) — this is convenient, since it means the same driver logic in
OpenWearOTA can largely ignore which transport is in use.

### 5.1 GATT fingerprint

| Role | UUID |
|---|---|
| Service | `f000ffc0-0451-4000-b000-000000000000` |
| "Identify" characteristic (commands, small control payloads) | `f000ffc1-0451-4000-b000-000000000000` |
| "Block" characteristic (bulk firmware data + most replies) | `f000ffc2-0451-4000-b000-000000000000` |

Beken's UUID base (`...-0451-4000-b000-000000000000`) is the once‑famous
**Texas Instruments CC254x/CC26xx SimpleBLE Profile UUID base** — Beken's reference
firmware was clearly bootstrapped from a TI BLE SDK sample project; this is a strong,
distinguishing fingerprint since no other family in this document shares that base.

### 5.2 Packet framing

Every command, in both directions, uses the same 4‑byte header:

```
byte 0    : command ID
byte 1    : frame sequence number (increments per command sent by the App; the App's
            own counter — the watch echoes it back so replies can be matched up)
byte 2-3  : payload length, little-endian uint16 (NOT counting this 4-byte header)
byte 4..  : payload (length given by bytes 2-3)
```

```python
def build_beken_frame(cmd_id: int, frame_seq: int, payload: bytes = b"") -> bytes:
    length = len(payload)
    return bytes([cmd_id, frame_seq & 0xFF, length & 0xFF, (length >> 8) & 0xFF]) + payload
```

Firmware data blocks are a special case of this same framing — command `5`, with the
payload itself prefixed by a 4‑byte little‑endian **file offset**:

```python
def build_beken_data_block(frame_seq: int, file_offset: int, chunk: bytes) -> bytes:
    payload = (
        file_offset.to_bytes(4, "little")
        + chunk
    )
    return build_beken_frame(5, frame_seq, payload)
```

### 5.3 Command table

| ID (dec) | Direction | Name | Payload |
|---|---|---|---|
| 1 | App→Watch | Request device/version info | none |
| 2 | Watch→App | Version info reply | 7 bytes; byte 6 (`bArr[10]` in the 4‑byte‑header‑relative original) is `1`=upper/app region active, `2`=lower/backup region active — tells you which flash bank is "live" right now |
| 3 | App→Watch | OTA request | first 32 bytes of the firmware file, verbatim, as a self‑describing header the watch parses |
| 4 | Watch→App | "Can OTA" reply | byte0: `1`=refused; otherwise payload carries 4‑byte starting file offset, 4‑byte requested length, and a 2‑byte block size the watch wants per `SendOTABlockData` burst |
| 5 | App→Watch | Firmware block | 4‑byte file offset + up to `negotiated block size` bytes of firmware |
| 6 | Watch→App | "Resend data block" | byte0 = the frame_seq to resume from, bytes 4‑7 = 4‑byte file offset to resume from — Beken's only loss‑recovery mechanism |
| 7 | App→Watch | "Send complete" | none |
| 8 | Watch→App | OTA done result | byte0: `0`=success, `1`=fail |
| 9 | Watch→App | "Update block length" notice | bytes 4‑5: 2‑byte block size the watch wants going forward (can change mid‑transfer) |
| 10 | App→Watch | ACK to a block‑length change | none — must be sent before resuming block transfer after a `9` |
| 11 | App→Watch | Reboot device | none — sent automatically after a successful `8` |

### 5.4 Flow

1. Connect, discover services, locate both characteristics, enable notify on the
   "block" characteristic.
2. Request MTU 512 if using BLE (classic SPP has no MTU concept — Beken's SDK simply
   skips this step for SPP and goes straight to step 3).
3. Send command `1` (get version/device info).
4. On command `2` reply: note which flash bank is active (informational only).
5. Send command `3` with the first 32 bytes of your firmware file as payload — this
   32‑byte header format is **opaque/vendor‑defined inside the firmware build itself**
   (FitPro just slices and forwards it, it does not interpret it); just take the first
   32 bytes of whatever `.bin` your firmware tool produced.
6. On command `4` reply: if refused, abort (commonly: wrong product/checksum mismatch
   detected by the watch's own bootloader from that 32‑byte header). Otherwise, record
   the starting offset/length/block‑size the watch told you to use.
7. Begin streaming command `5` blocks of `block_size` bytes each, advancing the file
   offset by `block_size` per block, until you've sent the full requested length.
8. If a command `6` ("resend") notification ever arrives mid‑transfer, **abandon your
   current position** and resume sending from the offset it specifies — this can happen
   any time, not just after errors (Beken's flow‑control is somewhat aggressive about
   this on slow/busy links).
9. If a command `9` ("block length changed") notification arrives, stop sending data,
   reply with command `10` (echoing back the frame_seq from the `9`), then resume
   sending blocks using the *new* block size from then on.
10. Once the file offset reaches the originally requested total length, send command
    `7` ("send complete").
11. Wait for command `8`. byte0 `0` = success. Either way, send command `11` (reboot)
    immediately afterward — Beken's own app does this unconditionally, even on
    reported failure, presumably to kick the watch back to a known state.

There is no checksum/CRC anywhere visible in this protocol at the application layer —
Beken's bootloader is trusted to do its own internal integrity check (and indeed, that's
exactly what the `4` "can OTA" 32‑byte header negotiation is for) — OpenWearOTA does
not invent one on top, to stay byte‑compatible with what real watches expect.

---

## 6. OnMicro protocol (OM6620 family)

OnMicro is the thinnest‑documented family in the FitPro bundle. The decompiled SDK
(`com.onmicro.omtoolbox`) gives us a complete, confirmed GATT fingerprint and the two
"entry" commands, but the deeper block‑transfer opcode sequence is implemented through
a third‑party Nordic Semiconductor BLE library (`no.nordicsemi.android.ble`) calling
into native code that JADX could not usefully decompile (it's likely closed‑source /
JNI on the OnMicro side). Treat this section as **less complete** than the other four —
good enough to detect an OnMicro chip reliably and to talk basic command/data framing,
but the full block‑ACK/retry semantics will need a live BLE capture (`nRF Connect` /
Wireshark + an Android HCI snoop log against the real FitPro app) to fill in before a
production‑quality implementation is possible.

### 6.1 GATT fingerprint

| Role | UUID |
|---|---|
| OTA service | `00001234-0000-1000-8000-00805f9b34fb` |
| OTA TX (app→watch) command channel | `0000ff01-0000-1000-8000-00805f9b34fb` |
| OTA TX (app→watch) data channel | `0000ff02-0000-1000-8000-00805f9b34fb` |
| OTA RX (watch→app) command channel | `0000ff03-0000-1000-8000-00805f9b34fb` |
| OTA RX (watch→app) data channel | `0000ff04-0000-1000-8000-00805f9b34fb` |
| ISP/bootloader service (chip not yet in app mode) | `6e40ff01-b5a3-f393-e0a9-e50e24dcca9e` |
| ISP TX | `6e40ff02-b5a3-f393-e0a9-e50e24dcca9e` |
| ISP RX | `6e40ff03-b5a3-f393-e0a9-e50e24dcca9e` |
| CCCD | `00002902-0000-1000-8000-00805f9b34fb` |

The ISP service UUID base (`b5a3-f393-e0a9-e50e24dcca9e`) is the **Nordic UART Service
(NUS)** base, just with OnMicro's own 16‑bit short‑UUID prefix (`6e40` instead of
Nordic's `6e40` — actually identical; OnMicro appears to have directly reused Nordic's
NUS as their ISP/bootloader transport verbatim). This is a useful fact for the UART
bridging feature (§8) — if you connect to a watch and see exactly this service, treat it
as a UART‑capable transport candidate even outside of OTA.

### 6.2 Known commands

```python
def om_enter_ota() -> bytes:
    return bytes([0x64, 0x00, 0x00, 0x00])     # sent on the OTA TX command channel

def om_get_isp_mac() -> bytes:
    return bytes([0x63, 0x00, 0x00, 0x00])     # sent on the ISP TX channel

def om_get_chip_info() -> bytes:
    # fixed packet, framed with literal STX/ETX-style markers (0x10 0x03)
    return bytes([0x10, 0x02, 0x10, 0x10, 0x07, 0x01,
                   0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
                   0x10, 0x03, 0x16])
```

`om_get_chip_info()`'s framing (`0x10 0x02 ... 0x10 0x03 <checksum>`) is a DLE/STX/ETX
byte‑stuffing pattern — `0x10` is being used as both a literal data byte further in and
as a frame marker, which is the classic "byte‑stuffed serial frame" approach also seen
in some other embedded UART protocols. OpenWearOTA implements send/receive for these
three known commands and surfaces whatever raw reply bytes come back to the user (for
manual inspection / further reverse engineering), rather than guessing at a full
block‑transfer state machine it can't verify.

### 6.3 Firmware package format

OnMicro firmware is shipped as a zip containing up to four `.bin` files, distinguished
by filename prefix:

| Prefix | Slot |
|---|---|
| `app_*.bin` | application firmware |
| `cfg_*.bin` / `config_*.bin` | configuration block |
| `patch_*.bin` | patch/delta image |
| `user_*.bin` | user data partition |

OpenWearOTA's OnMicro path accepts either a raw `.bin` (treated as `app`) or such a zip
(in which case it offers the user a choice of which slot(s) to flash) — actually
transferring those bytes uses the same TX data channel framing as the rest of FitPro's
GATT writes (chunked to MTU, written without response), but again: the precise
ACK/retry handshake around each chunk is the part not fully recovered from this
decompile. **Treat OnMicro support as "detect + best‑effort transfer" rather than a
fully verified implementation**, and prefer capturing a real transfer with FitPro
against a known OnMicro watch (Wireshark over an Android Bluetooth HCI snoop log is the
easiest way) if you need this path to be reliable.

---

## 7. RealSil protocol (RTL876x BLE audio/wearable SoC)

RealSil (Realtek) ships a professional DFU SDK (`com.realsil.android.blehub.dfu`,
Apache-2.0).  Unlike every other protocol in this document recovered from the FitPro
APK, this one was reverse-engineered from Realsil's *own* standalone SDK source —
`DfuService.java`, `BinInputStream.java`, and `RealsilDfu.java` — which is distributed
separately from FitPro and covers the same chip family.  The opcode constants are
legible and well-commented in the SDK source, so this section is significantly more
reliable than the OnMicro or older SHB sections.

RealSil chips are more common in TWS earbuds than $4 watches, but do appear in some
watch SKUs (especially ones marketed with "HD voice calling").

### 7.1 GATT fingerprint

| Role | UUID |
|---|---|
| Normal-firmware OTA-reset service (new spec) | `0000d0ff-3c17-d293-8e48-14fe2e4da212` |
| Normal-firmware OTA-reset service (legacy)   | `0000ffd0-0000-1000-8000-00805f9b34fb` |
| Normal-firmware OTA-reset characteristic     | `0000ffd1-0000-1000-8000-00805f9b34fb` |
| **DFU service** (active during phase 2)      | `00006287-3c17-d293-8e48-14fe2e4da212` |
| DFU data characteristic (write, no-response) | `00006387-3c17-d293-8e48-14fe2e4da212` |
| DFU control-point (write-with-response + notify) | `00006487-3c17-d293-8e48-14fe2e4da212` |
| CCCD (standard)                              | `00002902-0000-1000-8000-00805f9b34fb` |

OpenWearOTA's service-level detector (`detect.py`) matches on the DFU service UUID
`00006287-3c17-d293-8e48-14fe2e4da212`.

> **Note on UUID collision with Telink:** the `0000d0ff-3c17-...` OTA-reset service
> prefix is shared with Telink.  Disambiguate by checking for the DFU service
> `00006287-...` specifically — only genuine RealSil chips expose it.  OpenWearOTA's
> detection already does this correctly.

### 7.2 Two-phase OTA process

RealSil OTA operates in two distinct phases that may involve two separate BLE
connections:

**Phase 1 — App-mode handover** (`OTA_MODE_FULL_FUNCTION`):
Connect to the device running its normal firmware.  The normal firmware exposes the OTA
reset service (`0000d0ff-...` or `0000ffd0-...`).  Write a single byte `0x01` (write-
no-response) to the OTA reset characteristic (`0000ffd1-...`).  Wait ~1 second.  The
device reboots into DFU mode.  The DFU-mode device may advertise under a **different
MAC address** (the SDK scans for a device named `"BeeTgt"` or one whose manufacturer
data encodes the original MAC address in bytes 25-30 of the advertising payload).
Disconnect and re-scan for the DFU-mode device.

**Phase 2 — DFU transfer** (`OTA_MODE_LIMIT_FUNCTION`):
Connect to the DFU-mode device (which exposes `00006287-...`).  Use the control-point
and data characteristics to transfer the firmware image.

OpenWearOTA's `--limit-function` flag skips phase 1 when the device is already in DFU
mode.

### 7.3 .bin file header format (12 bytes, little-endian)

Parsed by `BinInputStream.parseBinFileHeader()`:

| Offset | Size | Field | Notes |
|---|---|---|---|
| 0 | uint16 | `offset` | flash write offset of the image body relative to app start |
| 2 | uint16 | `signature` | product/image type identifier — must match target's expected sig |
| 4 | uint16 | `version` | firmware version (checked if version-control is enabled) |
| 6 | uint16 | `checksum` | simple checksum of the image body |
| 8 | uint16 | `length` | image body length **in 4-byte units** (byte_len = length × 4) |
| 10 | uint8 | `ota_flag` | OTA flags |
| 11 | uint8 | `reserved_8` | reserved |

The image body immediately follows the 12-byte header.  `body_size = length × 4`.

### 7.4 Control-point opcode table

All commands are written to `DFU_CONTROL_POINT_UUID` (`00006487-...`) with
write-with-response.  Notifications arrive on the same characteristic (via CCCD).

| Opcode | Hex | Name | Payload | Notes |
|---|---|---|---|---|
| 1 | `0x01` | `START_DFU` | 17 bytes: opcode + 16 bytes AES-encrypted metadata | See §7.5 |
| 2 | `0x02` | `RECEIVE_FW_IMAGE` | 7 bytes: opcode + signature (uint16 LE) + offset (uint32 LE) | Arms the data channel |
| 3 | `0x03` | `VALIDATE_FW_IMAGE` | 3 bytes: opcode + signature (uint16 LE) | Triggers on-device CRC check |
| 4 | `0x04` | `ACTIVE_IMAGE_RESET` | 1 byte | Activates verified image and reboots; no response expected |
| 5 | `0x05` | `RESET` | 1 byte | Abort: cancel OTA and reset without activating |
| 6 | `0x06` | `REPORT_RECEIVED_IMAGE_INFO` | 3 bytes: opcode + signature (uint16 LE) | Query current image version and resume offset |
| 7 | `0x07` | `CONNECTION_PARAMETER_UPDATE` | 9 bytes: opcode + interval_min/max + latency + timeout (all uint16 LE) | Request faster connection; notification is informational, not waited on |

**Notification format:** every control-point notification is:
```
byte 0 : 0x10  (response type marker — DfuService checks this)
byte 1 : opcode being acknowledged
byte 2 : status code (see §7.6)
byte 3+: optional response payload
```
`REPORT_RECEIVED_IMAGE_INFO` response additionally carries:
```
byte 3-4 : originalVersion  (uint16 LE)
byte 5-8 : updateOffset     (uint32 LE) — resume byte offset into the image body, or 0
```

### 7.5 START_DFU payload (AES-256-ECB encrypted)

The 17-byte `START_DFU` command is opcode `0x01` followed by **16 bytes of
AES-256-ECB-encrypted metadata**.  The plaintext of those 16 bytes is:

```
bytes  0-1 : offset      (uint16 LE, from bin header)
bytes  2-3 : signature   (uint16 LE, from bin header)
bytes  4-5 : version     (uint16 LE, from bin header)
bytes  6-7 : checksum    (uint16 LE, from bin header)
bytes  8-9 : length      (uint16 LE, from bin header, 4-byte units)
byte  10   : ota_flag    (uint8)
byte  11   : reserved_8  (uint8)
bytes 12-15: 0x00 0x00 0x00 0x00   (AES block-alignment padding)
```

The default AES-256 key embedded in the SDK source is (hex):
```
4E 46 F8 C5 09 2B 29 E2  9A 97 1A 0C D1 F6 10 FB
1F 67 63 DF 80 7A 7E 70  96 0D 4C D3 11 8E 60 1A
```
This is a **default key** from Realsil's own SDK — production devices may use a
different per-product key provisioned at manufacture.  OpenWearOTA currently sends the
*plaintext* (unencrypted) payload, which will work on devices that have AES disabled
or that use the default key.  If your device requires AES, the encrypted bytes must be
computed externally and passed in.

### 7.6 DFU status codes (byte[2] of all notifications)

| Code | Hex | Name |
|---|---|---|
| 1 | `0x01` | `DFU_STATUS_SUCCESS` |
| 2 | `0x02` | `DFU_STATUS_NOT_SUPPORTED` |
| 3 | `0x03` | `DFU_STATUS_INVALID_PARAM` |
| 4 | `0x04` | `DFU_STATUS_OPERATION_FAILED` |
| 5 | `0x05` | `DFU_STATUS_DATA_SIZE_EXCEEDS_LIMIT` |
| 6 | `0x06` | `DFU_STATUS_CRC_ERROR` |

### 7.7 Full transfer flow

1. **(Phase 1 only)** Connect to normal firmware.  Write `0x01` (no-response) to
   OTA-reset char (`0000ffd1-...`).  Wait 1 s.  Disconnect.  Re-scan for DFU-mode
   device (name `"BeeTgt"` or MAC encoded in adv bytes 25-30).
2. Connect to DFU-mode device (DFU service `00006287-...`).  Enable notifications on
   control-point (CCCD write).
3. Write `CONNECTION_PARAMETER_UPDATE` (opcode `0x07`, 9 bytes).  No wait needed —
   the notification is informational.
4. Write `REPORT_RECEIVED_IMAGE_INFO` (opcode `0x06`, 3 bytes).  Wait for
   notification.  Parse `originalVersion` and `updateOffset`.
5. **If `updateOffset == 0`:** write `START_DFU` (opcode `0x01`, 17 bytes with AES-
   encrypted metadata).  Wait for `DFU_STATUS_SUCCESS` notification.
   **If `updateOffset != 0`:** skip START_DFU (resuming a previous interrupted OTA).
6. Write `RECEIVE_FW_IMAGE` (opcode `0x02`, 7 bytes: sig + offset).  No notification
   wait — the data channel is now armed.
7. Stream the firmware image body (skipping the 12-byte header and any already-received
   bytes) in chunks of ≤ 20 bytes (or `negotiatedMTU − 3`) to the data characteristic
   (`00006387-...`, write-no-response).  No per-packet ACK; the SDK is fire-and-forget
   here.
8. Write `VALIDATE_FW_IMAGE` (opcode `0x03`, 3 bytes).  Wait for
   `DFU_STATUS_SUCCESS` notification (device runs CRC check internally).
9. Write `ACTIVE_IMAGE_RESET` (opcode `0x04`, 1 byte).  Device reboots immediately —
   the write-response may not arrive.  On error at any step, write `RESET` (opcode
   `0x05`) before disconnecting.

### 7.8 Big-image handling (>100 KB)

`DfuService.java` contains a special-case for images larger than 100 KB: when the
streaming offset reaches exactly 104,000 bytes (`BIG_IMAGE_SPECIAL_POINT`), the SDK
*skips ahead* to byte offset 143,372 (140 KB − 12-byte header) and resumes streaming
from there.  This is an apparent workaround for a chip-side flash layout constraint
where bytes 104,000–143,372 are reserved/remapped.  OpenWearOTA does not currently
implement this skip — if you have a >100 KB firmware image that fails mid-transfer,
this may be the cause.

---

## 8. Auto‑detection algorithm

This is exactly what `openwearota/detect.py` implements, and mirrors what FitPro itself
does on every connection (each vendor SDK independently inspects `discoverServices()`
results — FitPro just runs all five inspectors and uses whichever one matches):

```python
def detect_chip_family(discovered_service_uuids: set[str]) -> str | None:
    u = {x.lower() for x in discovered_service_uuids}

    has_jieli   = "0000ae00-0000-1000-8000-00805f9b34fb" in u
    has_beken   = "f000ffc0-0451-4000-b000-000000000000" in u
    has_onmicro_ota = "00001234-0000-1000-8000-00805f9b34fb" in u
    has_onmicro_isp = "6e40ff01-b5a3-f393-e0a9-e50e24dcca9e" in u
    has_telink_ota  = "00010203-0405-0607-0809-0a0b0c0d1912" in u
    has_realsil_dfu = "00006287-3c17-d293-8e48-14fe2e4da212" in u
    has_shb     = "5833ff01-9b8b-5191-6142-22a4536ef123" in u
    has_slb     = "0000feb3-0000-1000-8000-00805f9b34fb" in u

    # Order matters only where two families could otherwise both match the same
    # "shared lineage" base UUID (Telink vs RealSil) -- check the more specific one
    # (RealSil's *additional* DFU service) first.
    if has_realsil_dfu:
        return "realsil"
    if has_telink_ota:
        return "telink"
    if has_jieli:
        return "jieli"
    if has_beken:
        return "beken"
    if has_onmicro_ota or has_onmicro_isp:
        return "onmicro"
    if has_shb or has_slb:
        return "shb_slb"
    return None
```

If nothing matches, OpenWearOTA falls back to listing every discovered service/
characteristic UUID for the user, so an unrecognized chip can still be manually
diagnosed (and, ideally, reported back so this table can grow).

Within the `shb_slb` family specifically, a **second** detection pass (§1.2) is needed
to disambiguate SHB‑app / SHB‑OTA / SLB‑upgrade mode, since which characteristics are
present (not just which service) determines the mode.

---

## 9. UART‑over‑BLE (for custom/MicroPython firmware)

Several of the SoC families above are popular targets for **community/custom firmware**
(most notably Telink TLSR8232, which has decent open‑source toolchain support, and to a
lesser extent JieLi). When someone flashes custom or MicroPython‑based firmware onto one
of these chips, they almost universally re‑use the chip's existing BLE stack to expose a
**transparent serial/UART bridge** characteristic pair, because writing a whole new BLE
GATT server just for a REPL is wasted effort when the OTA-capable BLE stack is already
sitting right there. There is no single standard for this — every custom firmware
project tends to invent its own characteristic pair — but there are exactly two
conventions you'll encounter in the wild, both of which OpenWearOTA supports:

### 9.1 Nordic UART Service (NUS) convention

By far the most common, because so many MCU vendors' BLE stacks (including, as noted in
§6.1, OnMicro's own ISP transport) ship Nordic's reference UART service verbatim, and
most hobbyist BLE‑UART firmware (including most ports of MicroPython's `bluetooth`
module reference examples) just copies Nordic's UUIDs rather than inventing new ones:

| Role | UUID |
|---|---|
| Service | `6e400001-b5a3-f393-e0a9-e50e24dcca9e` |
| TX (app writes here → device RX) | `6e400002-b5a3-f393-e0a9-e50e24dcca9e` |
| RX (app subscribes here → device TX, via notify) | `6e400003-b5a3-f393-e0a9-e50e24dcca9e` |

This is a plain transparent pipe: whatever bytes you write to the TX characteristic
appear on the device's UART RX line (and therefore, for a MicroPython port, in
`sys.stdin`/the REPL), and whatever the device prints to its UART TX line arrives as
notifications on the RX characteristic. No framing, no opcodes — it's just a serial
cable in disguise. OpenWearOTA's UART bridge mode (`openwearota uart`) defaults to this
convention and will auto‑detect it the same way it auto‑detects OTA chip families.

### 9.2 Vendor "ISP" UART convention

OnMicro's own bootloader/ISP channel (§6.1) uses the *same* Nordic NUS UUIDs, so it is
automatically covered by §9.1's detection — connecting to an OnMicro watch that hasn't
been told to enter OTA mode will, if it has a UART‑capable custom firmware loaded, "just
work" with the UART bridge without any extra configuration.

### 9.3 Custom/non‑standard UUIDs

If a particular custom firmware build uses its own UUIDs (this is common — many
hobbyist projects redefine the service UUID to avoid clashing with a chip's *other*,
simultaneously‑active BLE services), OpenWearOTA's UART bridge accepts explicit
`--service-uuid` / `--tx-uuid` / `--rx-uuid` overrides so it can still be pointed at any
arbitrary transparent‑serial characteristic pair, even ones not covered by either
convention above. If you're a custom‑firmware author and want your watch to be
auto‑detected without manual flags, the path of least resistance is to simply expose
the standard NUS UUIDs from §9.1 — OpenWearOTA (and a large fraction of existing mobile
BLE‑terminal apps) will then work against your firmware with zero configuration.

### 9.4 Practical notes

- Write characteristic is almost always used with **write‑without‑response** for
  throughput; if your firmware's BLE stack doesn't support that property on the TX
  characteristic, OpenWearOTA falls back to write‑with‑response automatically (slower,
  but still correct).
- There is no flow control at the BLE‑UART layer in either convention — if your
  firmware's incoming buffer is small, sending too fast can drop bytes. OpenWearOTA
  paces writes by default (one BLE write per line of input, with a small inter‑write
  delay) but this is tunable.
- MTU matters here too: a larger negotiated MTU means each notification can carry more
  bytes of REPL output per BLE event, which matters a lot for fast scrollback (e.g.
  pasting a multi‑line script into a MicroPython REPL). OpenWearOTA requests MTU 247
  (a safe, broadly‑supported value) on connect for UART sessions; some Android/iOS
  central stacks cap negotiated MTU regardless of what you request, this is a platform
  limitation, not an OpenWearOTA one — on Linux/BlueZ thanks to `bleak`, in practice you
  usually get what you ask for, up to what the remote chip itself supports.

---

## 10. Sources and provenance

Most of the claims above were extracted from a JADX decompile of a build of the
"FitPro" Android app (package names referenced throughout: `com.ota.otalib`,
`com.telink.ota`, `com.jieli.jl_bt_ota`, `com.beken.beken_ota`, `com.onmicro.omtoolbox`,
`com.realsil.sdk.dfu`/`com.realsil.sdk.bbpro`). Where a detail could not be confirmed
(RealSil's exact control‑point opcodes, OnMicro's full block‑transfer handshake) this
document says so explicitly rather than guessing and presenting a guess as fact.

§1.8b is the one exception: it is transcribed directly from PhyPlus's own
`PHY62XX_SDK_3.1.1` (`components/profiles/ota/ota_service.c` + `ota_protocol.c`,
`components/libraries/crc16/crc16.c`), the chip vendor's official, publicly‑distributed
SDK — no decompilation involved, just reading vendor source. That section is
correspondingly held to a higher confidence bar than the rest of this document, and is
flagged as such inline.

If you extend this reference with a confirmed correction or a newly reverse‑engineered
chip family, please keep the same standard: cite what you actually observed (decompiled
code path, vendor SDK source path, or a packet capture), and mark anything inferred/
best‑effort as such.

---