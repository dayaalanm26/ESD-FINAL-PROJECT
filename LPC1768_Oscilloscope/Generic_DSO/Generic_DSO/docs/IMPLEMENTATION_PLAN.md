# Generic DSO Implementation Plan

Goal: deliver a hardware-only native DSO application with generic channel metadata.
Architecture: protocol and numerical core independent of Qt; background I/O worker;
native GUI receives validated frames and renders captured measurements.
Tech stack: Python 3.10+, PySide6, PyQtGraph, NumPy, pyserial.
Spec: DESIGN.md. Execution: native, following the user's approval to start.

## Tasks
- [x] Add protocol tests for valid frames, bad metadata, nonfinite samples,
      out-of-order timestamps, packet loss and jitter-aware frequency measurement.
- [x] Implement scope_core.py with decoder, capture engine and measurements.
- [x] Implement transport.py: serial/TCP reader, bounded queue, timeouts and shutdown.
- [x] Implement scope_app.py: flat DSO screen, channel calibration, trigger controls,
      cursors, disconnected state, capture exports and configuration persistence.
- [x] Add real ADC Arduino example and generic C sender; document protocol and
      Python microprocessor sensor adapter with no random/synthetic data source.
- [x] Check offscreen UI, cursor calculations, frozen capture, trigger behavior,
      parser and a pseudo-terminal integration path.
- [x] Package double-click Windows launcher, requirements, guides and tests.

## Review focus
1. Packet loss must never join non-contiguous segments into a triggered capture.
2. Units and calibration must follow the selected channel in trigger/cursor values.
3. No hardware/no samples must leave waveform and measurements empty.
4. Stop/Single must preserve the capture while acquisition transport keeps reading.
5. Malformed or oversized serial messages must not crash or exhaust the UI.
