from __future__ import annotations


def crc16_xmodem(data: bytes, crc: int = 0xFFFF) -> int:
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if (crc & 0x8000) else (crc << 1)
        crc &= 0xFFFF
    return crc


def crc16_arc(data: bytes) -> int:
    crc = 0
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if (crc & 1) else (crc >> 1)
    return crc & 0xFFFF


def telink_crc16(buf18: bytes) -> int:
    table = (0x0000, 0xA001)
    crc = 0xFFFF
    for byte in buf18:
        b = byte
        for _ in range(8):
            crc = (table[(crc ^ b) & 1] ^ (crc >> 1)) & 0xFFFF
            b >>= 1
    return crc