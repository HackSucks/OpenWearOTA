from __future__ import annotations
import asyncio
import sys

NUS_SERVICE = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
NUS_TX      = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
NUS_RX      = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"
MTU_TARGET  = 247
PACE_S      = 0.02


def detect_uart_service(discovered_service_uuids: set[str]) -> bool:
    return NUS_SERVICE.lower() in {u.lower() for u in discovered_service_uuids}


async def run_uart_bridge(client, service_uuid=NUS_SERVICE, tx_uuid=NUS_TX,
                          rx_uuid=NUS_RX, pace_s=PACE_S) -> None:
    print(f"[*] UART bridge active  (TX-> {tx_uuid})")
    print(f"                        (RX<- {rx_uuid})")
    print("[*] Type to send. Ctrl-C to exit.\n")

    def _rx_handler(_sender, data: bytearray):
        sys.stdout.write(data.decode("utf-8", errors="replace"))
        sys.stdout.flush()

    await client.start_notify(rx_uuid, _rx_handler)
    loop = asyncio.get_event_loop()
    try:
        while True:
            line = await loop.run_in_executor(None, sys.stdin.readline)
            if not line:
                break
            raw = line.encode("utf-8")
            try:
                await client.write_gatt_char(tx_uuid, raw, response=False)
            except Exception:
                await client.write_gatt_char(tx_uuid, raw, response=True)
            if pace_s > 0:
                await asyncio.sleep(pace_s)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await client.stop_notify(rx_uuid)
        print("\n[*] UART bridge closed.")