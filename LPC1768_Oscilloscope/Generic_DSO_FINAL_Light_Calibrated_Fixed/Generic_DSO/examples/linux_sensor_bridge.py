"""Bridge an actual Linux IIO sensor raw attribute to Generic DSO over TCP.

Example on a board exposing IIO (use the REAL existing path for your sensor):
python linux_sensor_bridge.py /sys/bus/iio/devices/iio:device0/in_voltage0_raw --unit count

No generated fallback. Fails if the sensor cannot be read. Polling timestamps
describe software reads, not the sensor's internal conversion/analog timing.
Use a hardware-buffered IIO driver for precision waveform capture.
"""
import argparse
import json
import math
from pathlib import Path
import socket
import time

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('sensor_path',type=Path)
p.add_argument('--unit',default='count'); p.add_argument('--gain',type=float,default=1)
p.add_argument('--offset',type=float,default=0); p.add_argument('--interval',type=float,default=.05)
p.add_argument('--host',default='127.0.0.1'); p.add_argument('--port',type=int,default=9000)
a=p.parse_args()
if a.interval<=0: p.error('--interval must be positive')
float(a.sensor_path.read_text()) # fail before opening a server if no real sensor exists
with socket.socket() as server:
    server.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
    server.bind((a.host,a.port)); server.listen(1)
    print(f'Reading {a.sensor_path}; listening at {a.host}:{a.port}')
    while True:
        conn,address=server.accept()
        with conn:
            def send(obj): conn.sendall((json.dumps(obj,allow_nan=False)+'\n').encode())
            try:
                send({'type':'hello','protocol':1,'device':'Linux IIO sensor bridge',
                      'channels':[{'name':a.sensor_path.name[:40],'unit':a.unit}]})
                start=time.monotonic(); seq=0
                while True:
                    value=float(a.sensor_path.read_text())*a.gain+a.offset
                    if not math.isfinite(value): raise ValueError('Invalid sensor value')
                    stamp=time.monotonic()-start
                    send({'type':'samples','seq':seq,'times':[stamp],'values':[[value]]})
                    seq+=1; time.sleep(a.interval)
            except (BrokenPipeError,ConnectionResetError): pass
