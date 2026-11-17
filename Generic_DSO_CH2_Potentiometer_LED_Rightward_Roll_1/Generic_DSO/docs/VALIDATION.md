# Validation — lavender redesign

- Full command: `QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -v`.
- Result: **53 tests passed**, process exit code 0; full output in TEST_RESULTS.txt.
- Environment: Linux, Python 3.12, PySide6 6.12.0, pyqtgraph 0.14.0, NumPy 2.3.5.
- Original protocol/capture/transport tests remain. Transport uses a real OS pseudo-terminal.
- Added regressions: selected-menu styling, centralized run state, shared AC reference,
  probe scale/position/trigger preservation, offscreen cursor recentering, empty/stale
  measurements, interpolation isolation, raw CSV export, acquisition resets, held
  processed captures, redraw coalescing and long measurement text.
- Numerical checks: last-N triggered averaging, length/timebase reset, untriggered
  exclusion, Hi-Res boxcar, original extrema preservation and irregular/dense fallback.
- Rendered and inspected 1280×800, 1440×940 and 1920×1080 screenshots with a 500 Hz
  CH1 sine and CH2 square wave. Extra 1280×800 fixture checks acceleration units
  and scientific notation. No control overlap or clipped measurement text found
  for these fixtures. All six menus retain the permanent acquisition buttons.
- Synthetic data exists only in tests; the normal application has no signal source.
- An independent code review identified held-processing loss, redraw throttling
  and long-unit clipping; all three were fixed with failing-then-passing regressions.
- Test Qt widgets are explicitly destroyed before QApplication teardown to avoid
  interpreter-shutdown graphics ownership errors.

Not tested: physical ADC hardware, electrical calibration, Windows execution of
batch launchers, or a packaged Windows executable. Screenshots are software fixtures,
not evidence of hardware accuracy or bandwidth. Qt logical window dimensions may
require more physical pixels when operating-system display scaling is enabled.

## Update: light Tek reference edition — 2026-10-25

The lavender validation above is **historical for the uploaded base**, not a validation of the new theme. For the latest edited release, Python static compilation and 28 numerical tests (18 `test_core`, 6 `test_processing`, 4 `test_calibration`) pass in the offline container. GUI tests and Windows execution are **not verified in this environment** because PySide6 and pyqtgraph were unavailable to install. Run the Qt GUI tests and `tests/render_preview.py` on your Windows laptop after dependencies are installed. Electrical calibration also needs the physical ESP32 divider and oscilloscope; do not infer 3.3 Vpp measurement precision from the reference photo alone.
