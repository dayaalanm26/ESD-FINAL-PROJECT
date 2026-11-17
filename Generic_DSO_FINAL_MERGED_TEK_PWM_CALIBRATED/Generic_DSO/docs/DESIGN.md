# Generic DSO — classic DSO layout

The application separates hardware capture from display processing. `transport.py`
and the decoder/capture engine in `scope_core.py` retain the original protocol and
record selection. Only empty measurement handling changed in the core module.

`theme.py` owns the palette and Qt stylesheet. `scope_app.py` provides the header,
plot, measurement chips, channel cards and persistent right-side controls.
`display_processing.py` provides rolling triggered averaging, adjacent-sample
Hi-Res processing and a display-only windowed-sinc interpolator.

`current` always holds the calibrated, unfiltered captured record. `processed_y`
is a separate acquisition copy; the record's AC reference is held alongside it.
Changing acquisition settings resets the averaging accumulator but preserves a
stopped processed record until a new capture or explicit mode change replaces it.
The display interpolation output is never used for measurements or CSV export.

A precise single-shot redraw timer coalesces changes to at most 30 fps. Curves
retain peak downsampling and original sample extrema. A layout-owned sidebar
ranges from 268 to 292 logical pixels, keeping the scope view flexible. Measurement
readings fit their font size before eliding unusually long custom units (full value
available in a tooltip). No widget uses fixed absolute positions.
