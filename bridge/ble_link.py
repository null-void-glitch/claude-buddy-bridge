"""BLE transport to the Cardputer over the Nordic UART Service.

Pairing/bonding must be done once via `bluetoothctl` (see README) before
this can connect: the NUS characteristics require encryption, and BlueZ
needs an existing bond to satisfy that without an interactive pairing
agent here.
"""
import asyncio
import json
import logging
import threading

from bleak import BleakClient, BleakScanner

log = logging.getLogger("buddy.ble")

SERVICE_UUID = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
RX_CHAR_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"  # write: host -> device
TX_CHAR_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"  # notify: device -> host

SCAN_TIMEOUT = 10.0
CONNECT_TIMEOUT = 20.0
RECONNECT_DELAY = 3.0


async def _scan(timeout):
    devices = await BleakScanner.discover(timeout=timeout)
    return [(d.name, d.address) for d in devices if d.name and d.name.startswith("Claude-")]


def find_device_sync(timeout=SCAN_TIMEOUT):
    found = asyncio.run(_scan(timeout))
    if not found:
        raise RuntimeError(
            "No 'Claude-XXXX' BLE device found. Is it powered on and already "
            "paired via `bluetoothctl` (see README)?"
        )
    if len(found) > 1:
        raise RuntimeError(f"Multiple devices found: {found}. Pass --address explicitly.")
    return found[0][1]


class BLELink:
    """Runs its own asyncio event loop on a background thread; exposes a
    thread-safe, synchronous send() plus an on_line callback for incoming
    JSON lines, mirroring the interface a stream-based transport would have.
    """

    def __init__(self, address, on_line, on_reconnect=None):
        self._address = address
        self._on_line = on_line
        self._on_reconnect = on_reconnect
        self._loop = asyncio.new_event_loop()
        self._client = None
        self._buf = b""
        self._ready = threading.Event()
        self._ready_error = None
        self._stop_evt = None
        self._thread = threading.Thread(target=self._run_loop, daemon=True)

    def start(self):
        self._thread.start()
        if not self._ready.wait(timeout=CONNECT_TIMEOUT):
            raise RuntimeError("timed out connecting to the device over BLE")
        if self._ready_error:
            raise self._ready_error

    def stop(self):
        if self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._shutdown(), self._loop)
        self._thread.join(timeout=5)

    def send(self, obj):
        line = (json.dumps(obj, separators=(",", ":")) + "\n").encode("utf-8")
        fut = asyncio.run_coroutine_threadsafe(self._write(line), self._loop)
        try:
            fut.result(timeout=10)
        except Exception:
            log.exception("BLE write failed")
            fut.cancel()

    def _run_loop(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._main())

    async def _main(self):
        self._stop_evt = asyncio.Event()
        self._disconnected_evt = asyncio.Event()
        try:
            await self._connect_once()
        except Exception as e:
            self._ready_error = e
            self._ready.set()
            return
        self._ready.set()

        while not self._stop_evt.is_set():
            stop_task = asyncio.create_task(self._stop_evt.wait())
            disconnected_task = asyncio.create_task(self._disconnected_evt.wait())
            await asyncio.wait(
                [stop_task, disconnected_task], return_when=asyncio.FIRST_COMPLETED
            )
            if self._stop_evt.is_set():
                disconnected_task.cancel()
                try:
                    await self._client.disconnect()
                except Exception:
                    pass
                return
            stop_task.cancel()
            log.warning("BLE link dropped, reconnecting to %s", self._address)
            while not self._stop_evt.is_set():
                try:
                    await self._connect_once()
                    log.info("BLE link restored")
                    if self._on_reconnect:
                        try:
                            self._on_reconnect()
                        except Exception:
                            log.exception("on_reconnect callback failed")
                    break
                except Exception:
                    log.exception("reconnect attempt failed, retrying")
                    await asyncio.sleep(RECONNECT_DELAY)

    async def _connect_once(self):
        self._disconnected_evt.clear()
        self._client = BleakClient(self._address, disconnected_callback=self._on_disconnect)
        await asyncio.wait_for(self._client.connect(), timeout=CONNECT_TIMEOUT)
        await asyncio.wait_for(
            self._client.start_notify(TX_CHAR_UUID, self._notify_handler), timeout=CONNECT_TIMEOUT
        )

    def _on_disconnect(self, _client):
        log.warning("device disconnected")
        self._disconnected_evt.set()

    async def _shutdown(self):
        if self._stop_evt:
            self._stop_evt.set()

    async def _write(self, data):
        mtu = (getattr(self._client, "mtu_size", None) or 23) - 3
        mtu = max(mtu, 20)
        for i in range(0, len(data), mtu):
            await self._client.write_gatt_char(RX_CHAR_UUID, data[i:i + mtu])

    def _notify_handler(self, _sender, data):
        self._buf += bytes(data)
        while b"\n" in self._buf:
            line, self._buf = self._buf.split(b"\n", 1)
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line.decode("utf-8", "replace"))
            except json.JSONDecodeError:
                continue
            try:
                self._on_line(obj)
            except Exception:
                log.exception("on_line handler failed")
