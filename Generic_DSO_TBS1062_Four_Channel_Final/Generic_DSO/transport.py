"""Read-only transport. Hardware packets only; never generates fake scope samples.

This reader favors the freshest received frames when the UI is temporarily slow.
A bounded queue preserves device identification and connection/error messages.
"""
from collections import deque
import queue
import socket
import threading
import time
from scope_core import Decoder, Frame, ProtocolError, MAX_LINE


class RealtimeEventQueue:
    """Thread-safe queue that never allows old samples to block live acquisition.

    When full, discard the oldest FRAME, not a metadata/error event. A new
    sequence discontinuity is indicated by the reader; lost samples are never
    silently interpolated by the numerical capture engine.
    """
    def __init__(self, maxsize=128):
        self.maxsize=maxsize
        self._events=deque()
        self._cv=threading.Condition()
        self.dropped=0

    def put(self, item, timeout=None):
        with self._cv:
            if len(self._events)>=self.maxsize:
                index=next((i for i, (_, value) in enumerate(self._events)
                            if isinstance(value, Frame)), None)
                if index is None:
                    raise queue.Full('Queue contains only critical connection events')
                del self._events[index]
                self.dropped+=1
                # A retained frame after a discarded one must not look
                # temporally continuous to the capture engine.
                for event_kind, event_value in self._events:
                    if isinstance(event_value, Frame):
                        event_value.gap=True
                        break
            self._events.append(item)
            self._cv.notify()

    def get_nowait(self):
        return self.get(block=False)

    def get(self, block=True, timeout=None):
        with self._cv:
            if not block and not self._events:
                raise queue.Empty
            if block and not self._events:
                self._cv.wait_for(lambda:bool(self._events),timeout=timeout)
            if not self._events:
                raise queue.Empty
            return self._events.popleft()

    def qsize(self):
        with self._cv:
            return len(self._events)


class Reader(threading.Thread):
    def __init__(self, transport, address, rate):
        super().__init__(daemon=True)
        self.transport, self.address, self.rate=transport,address,rate
        self.events=RealtimeEventQueue(maxsize=128)
        self.stop_event=threading.Event()
        self.invalid=self.dropped=0
        self.last_error=''
        self.link=None

    def post(self, kind, value):
        try:
            self.events.put((kind, value))
            self.dropped=self.events.dropped
        except queue.Full:
            if isinstance(value, Frame):
                self.dropped+=1
            else:
                raise RuntimeError('Receive queue filled with connection events')

    def close(self):
        self.stop_event.set()
        if self.link is not None:
            try: self.link.close()
            except (OSError, AttributeError): pass

    def run(self):
        decoder, buffer=Decoder(),bytearray()
        last_dropped=0
        try:
            if self.transport=='Serial':
                import serial
                # A short timeout lets the reader respond to stop requests
                # without holding up the GUI or the stream.
                self.link=serial.Serial(self.address,self.rate,timeout=.08)
                read=lambda: self.link.read(min(max(self.link.in_waiting,1),65536))
            else:
                self.link=socket.create_connection((self.address,self.rate),timeout=3)
                self.link.settimeout(.08)
                read=lambda: self.link.recv(65536)
            self.post('connected',self.address)
            while not self.stop_event.is_set():
                try: chunk=read()
                except socket.timeout: continue
                if not chunk:
                    if self.transport=='TCP': raise ConnectionError('Device closed the connection')
                    continue
                buffer.extend(chunk)
                while b'\n' in buffer:
                    line,_,buffer=buffer.partition(b'\n')
                    if not line.strip():continue
                    try:
                        item=decoder.decode(line)
                    except (ProtocolError,UnicodeError) as exc:
                        self.invalid+=1
                        self.last_error=str(exc)
                        continue
                    if isinstance(item,Frame) and self.dropped!=last_dropped:
                        item.gap=True
                        last_dropped=self.dropped
                    self.post('data',item)
                if len(buffer)>MAX_LINE:
                    raise ProtocolError('Unterminated/oversized message; maximum 2 MB')
        except Exception as exc:
            if not self.stop_event.is_set():
                self.last_error=str(exc)
                try: self.post('error',str(exc))
                except RuntimeError:pass
        finally:
            self.close()
