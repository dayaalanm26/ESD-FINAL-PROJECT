"""Read-only transport. Never sends actuator commands or fabricated samples."""
import queue
import socket
import threading
from scope_core import Decoder, Frame, ProtocolError, MAX_LINE


class Reader(threading.Thread):
    def __init__(self, transport, address, rate):
        super().__init__(daemon=True)
        self.transport, self.address, self.rate = transport, address, rate
        self.events = queue.Queue(maxsize=128)
        self.stop_event = threading.Event()
        self.invalid = self.dropped = 0
        self.last_error = ''
        self.link = None

    def post(self, kind, value):
        try:
            self.events.put((kind, value), timeout=.1)
        except queue.Full:
            if isinstance(value, Frame): self.dropped += 1
            else: raise RuntimeError('Receive queue full; reconnect at a lower rate')

    def close(self):
        self.stop_event.set()
        if self.link is not None:
            try: self.link.close()
            except (OSError, AttributeError): pass

    def run(self):
        decoder, buffer = Decoder(), bytearray()
        last_dropped = 0
        try:
            if self.transport == 'Serial':
                import serial
                self.link = serial.Serial(self.address, self.rate, timeout=.2)
                read = lambda: self.link.read(min(max(self.link.in_waiting,1),65536))
            else:
                self.link = socket.create_connection((self.address, self.rate),timeout=3)
                self.link.settimeout(.2)
                read = lambda: self.link.recv(65536)
            self.post('connected', self.address)
            while not self.stop_event.is_set():
                try: chunk = read()
                except socket.timeout: continue
                if not chunk:
                    if self.transport == 'TCP': raise ConnectionError('Device closed the connection')
                    continue
                buffer.extend(chunk)
                while b'\n' in buffer:
                    line, _, buffer = buffer.partition(b'\n')
                    if not line.strip(): continue
                    try:
                        item = decoder.decode(line)
                    except (ProtocolError, UnicodeError) as exc:
                        self.invalid += 1
                        self.last_error = str(exc)
                        continue
                    if isinstance(item, Frame) and self.dropped != last_dropped:
                        item.gap = True
                        last_dropped = self.dropped
                    self.post('data',item)
                if len(buffer) > MAX_LINE:
                    raise ProtocolError('Unterminated/oversized message; maximum 2 MB')
        except Exception as exc:
            if not self.stop_event.is_set():
                self.last_error = str(exc)
                try: self.post('error',str(exc))
                except RuntimeError: pass
        finally:
            self.close()
