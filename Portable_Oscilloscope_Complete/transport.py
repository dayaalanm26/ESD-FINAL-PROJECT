"""Background acquisition for USB serial and legacy TCP DSO streams."""
from collections import deque
import socket
from threading import Event, Lock, Thread

from scope_data import Decoder, MAX_LINE, Packet, ProtocolError


def serial_ports():
    try:
        from serial.tools import list_ports
    except ImportError:
        return []
    return [item.device for item in sorted(list_ports.comports())]


class Reader:
    def __init__(self, source, address, rate):
        self.source = source
        self.address = address
        self.rate = rate
        self.stopped = Event()
        self.lock = Lock()
        self.events = deque()
        self.invalid = 0
        self.dropped = 0
        self.decoder = Decoder()
        self.last_device = None
        self.thread = Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()

    def close(self):
        self.stopped.set()

    def take(self):
        with self.lock:
            events = list(self.events)
            self.events.clear()
            return events, self.invalid, self.dropped

    def _post(self, event, value):
        with self.lock:
            if len(self.events) >= 96:
                for i, (_, item) in enumerate(self.events):
                    if isinstance(item, Packet):
                        del self.events[i]
                        self.dropped += 1
                        break
                else:
                    self.events.popleft()
                    self.dropped += 1
            self.events.append((event, value))

    def _parse(self, line):
        if not line.strip():
            return
        try:
            item = self.decoder.decode(line)
        except ProtocolError:
            with self.lock:
                self.invalid += 1
            return
        if isinstance(item, Packet):
            self._post("samples", item)
        elif item is not None and item != self.last_device:
            self._post("device", item)
            self.last_device = item

    def _open(self):
        if self.source == "USB serial":
            try:
                import serial
            except ImportError as exc:
                raise RuntimeError("Install pyserial using Start_DSO.bat or requirements.txt.") from exc
            return serial.Serial(self.address, self.rate, timeout=0.04)
        link = socket.create_connection((self.address, self.rate), timeout=3)
        link.settimeout(0.07)
        return link

    def _run(self):
        pending = bytearray()
        try:
            with self._open() as link:
                self._post("opened", self.address)
                while not self.stopped.is_set():
                    if self.source == "USB serial":
                        chunk = link.read(min(max(1, link.in_waiting), 131072))
                    else:
                        try:
                            chunk = link.recv(65536)
                        except socket.timeout:
                            continue
                        if not chunk:
                            raise ConnectionError("TCP device closed the connection.")
                    if not chunk:
                        continue
                    pending.extend(chunk)
                    tail = pending.rfind(b"\n")
                    if tail >= 0:
                        for line in pending[:tail].split(b"\n"):
                            if len(line) > MAX_LINE:
                                raise ProtocolError("Received packet exceeds the size limit.")
                            self._parse(line)
                        del pending[:tail + 1]
                    if len(pending) > MAX_LINE:
                        raise ProtocolError("No complete DSO packets. Check baud or device firmware.")
        except (OSError, RuntimeError, ValueError) as exc:
            if not self.stopped.is_set():
                self._post("error", str(exc))
