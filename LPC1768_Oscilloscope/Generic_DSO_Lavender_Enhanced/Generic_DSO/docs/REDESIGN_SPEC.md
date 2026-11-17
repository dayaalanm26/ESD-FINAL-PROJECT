You are a senior PySide6 + pyqtgraph developer and instrument-UI designer. Redesign and fix the attached "Generic DSO" oscilloscope app (scope_app.py, scope_core.py, transport.py, tests/, README.md, launchers). Keep every existing feature and the real-hardware-only behaviour (no generated signals). Return the complete updated files, not snippets.

## 1. Lavender theme
Replace the blue Tek-style theme with one lavender palette defined once as constants and used everywhere (no stray hex values in widgets):
- App background #1E1B2E · panels #2A2540 · cards/inputs #342E4F · borders #4A4170
- Accent lavender #B8A4F4 (hover #C9B8FF, pressed #9C86E0), accent text #1E1B2E
- Soft-menu surface #E6DEFF with text #2B2350
- Text #EEEAF8 · muted text #A59CC4
- Scope screen #0E0C17 · major grid #3A3358 (dotted) · centre axes #5A4F86 with 5 minor ticks per division
- Traces: CH1 #FFD84A, CH2 #3EE6FF, CH3 #FF6FB5 (replaces the purple, which would disappear against lavender), CH4 #6EF0A0
- Trigger #FF9E57 · cursors white at 75% · status: OK #6EF0A0, warning #FFC857, error #FF6B81
- Fonts: "Segoe UI", "Inter", "Helvetica Neue", sans-serif. Monospace readouts: "Consolas", "JetBrains Mono", "DejaVu Sans Mono", monospace (the current fonts only exist on Windows).
- 6 px corner radius on cards and buttons, 1 px borders, no heavy outlines.

## 2. Layout redesign
- **Header (one row):** "Generic DSO" on the left; Serial/TCP selector, port, baud/port, a refresh icon button and Connect next to it; on the right a coloured state pill (Disconnected / Ready / Running / Stopped / Armed) and a gear button for Device & sensors. Delete the separate connection row so the screen gets more height.
- **Screen:** 10 × 8 graticule. Slim readout bars at the top (trigger state, horizontal position, mode) and bottom (V/div for each active channel, s/div, trigger source and level, frequency, sample rate). Show a small coloured "1▸ 2▸" ground marker for each visible channel on the left edge, a trigger-level arrow on the right edge and a "T" marker on the top edge.
- **Right panel (~280 px):** Put RUN/STOP (large, green when running, red when stopped), SINGLE and AUTOSET at the top so they're always visible, like real front-panel buttons. Below them, a segmented tab bar (Acquire · Horizontal · Trigger · Cursor · Measure · Save) and then the selected page. The active tab must be highlighted.
- **Channel strip:** four compact cards. Each card has a coloured header chip that toggles the channel, one V/div slider with a compact value field, one position slider with a compact value field (unit suffixes on both), DC/AC/GND as a segmented control, and a probe dropdown. Disabled channels show greyed out without empty labels.
- **Measurements:** one row of 8 compact chips under the screen (small caps label, monospace value, 4 significant figures).
- Minimum window 1280 × 800. Use layouts and stretch factors only, with no fixed pixel positions. Nothing may clip at 1280 × 800 or at 1920 × 1080.

## 3. Waveform: thicker, smoother, cleaner
- Trace pen: 2.8 px, cosmetic, antialiased, Qt.RoundCap and Qt.RoundJoin.
- Add a soft phosphor glow under each trace: a second curve in the same colour, 7 px wide, at 18% alpha, drawn first.
- Add a **Display interpolation** option on the Acquire page: "Sin(x)/x" (default) or "Linear". Sin(x)/x applies only when there are fewer than about 4 samples per screen pixel and must be done in a display-only copy. Measurements, cursors and CSV export always use the raw samples.
- Add an **Acquisition mode** option: Sample (default), Average (N = 2/4/8/16/32/64 over successive triggered records with the same length and time base; reset whenever V/div, s/div, trigger or coupling changes) and Hi-Res (boxcar over adjacent samples). Label the mode in the top readout bar. In Average and Hi-Res mode, measurements use the processed data, as on a real scope.
- Keep peak downsampling so narrow spikes are never lost. Use setClipToView(True), skipFiniteCheck=True and a 30 fps redraw cap.
- Only show sample dots when there are fewer than 80 points on screen, at size 4, in the channel colour.
- Lower the grid opacity so the traces stand out.

## 4. Errors to correct
1. The "Device & sensors" button shows as "Device _sensors" because Qt treats & as a mnemonic. Escape it as "&&" (or use the gear icon with a tooltip).
2. The active soft-menu key is never highlighted. show_menu() sets a "selected" property, but no stylesheet rule uses it and the style is never re-polished. Add a [selected="true"] rule and call unpolish/polish.
3. The Run button label changes inconsistently ("RUN / STOP" → "Run" → "RUN" / "STOP"). Drive the label and colour from one set_run_state() method.
4. Channel labels duplicate names, e.g. "CH3  CH3 [V]". Show "CH1 · A0 (V)", or only "CH3 (V)" when the name equals the channel id.
5. With AC coupling, the trigger doesn't match the display. The display subtracts the capture mean, but trigger_engine_level() adds the mean of the whole 65,536-point buffer. Use the same reference for both (for example, the mean of the last record span).
6. Changing the probe from 1X to 10X/100X makes the trace jump and lose the trigger. When the probe changes, scale that channel's position and (if it is the trigger source) the trigger level by new/old factor.
7. The time cursors start at ±2 ms, so they are off-screen at most time bases. Whenever cursors are switched on or s/div changes and a cursor is out of view, move them to 25% and 75% of the visible window.
8. measurements() returns None for an empty array, and callers then index it, which crashes. update_measurements() also returns early and leaves stale readings when the measurement channel is out of range. Guard both and clear the readings to "—".
9. The probe and trigger-level spin boxes have no unit. Add suffixes that follow the channel unit.
10. Remove the dead autoscale() alias.
11. Use one spelling convention throughout ("Center").
12. Merge Start_DSO.bat and Start_DSO_FIXED.bat into a single Start_DSO.bat that uses the "generic_dso" Conda env if it exists and otherwise creates or uses .venv.
13. Remove the "Tek" logo badge and every "Tektronix" reference in the UI, window title, README and tests. It is a third-party trademark. Name the design "classic DSO layout".

## 5. Cut the excess wording
Replace or delete these:
- Subtitle "TEKTRONIX-STYLE HARDWARE OSCILLOSCOPE" → delete
- Window title → "Generic DSO"
- Empty screen "Connect acquisition hardware / Real ADC samples appear here" → "No signal"
- Acquire note "Continuous, normal and single acquisition use real timestamped ADC samples." → delete
- Trigger note "Edge trigger · 20% pre-trigger" → move to a tooltip
- Measure page list that repeats the measurement cards → replace with checkboxes that choose which chips are shown
- Footer "Hardware only • No samples received • Accuracy requires hardware calibration" and "Unfiltered • calibration unverified" → a compact footer "Frames 1234 · Gaps 0 · Invalid 0 · Drops 0", with errors and clipping shown as a red badge
- Status text → short words: CONNECTING, WAITING, READY, RUN, TRIG'D, AUTO, ARMED, STOP, HOLD
- "AUTORANGE · CONTINUOUS" → "Autorange"; "EXPORT SAMPLES · CSV" → "Export CSV"; "SAVE SCREEN · PNG" → "Screenshot"; "CENTRE CURSORS" → "Center cursors"
- Cursor readout placeholders → "—"
- "Uniform · 50 kSa/s median · 500 pts" → "50 kSa/s · 500 pts" (add "irregular" only when the timing is irregular)
- Profile dialog: replace the two disclaimer paragraphs with a single line, "Value = raw × gain + offset"
- Connection-guide dialog: at most 4 short steps
Use tooltips for any explanation that's still useful.

## 6. Delivery and checks
- Keep scope_core.py's protocol and capture logic unchanged apart from the fixes above. Put theme constants and the stylesheet in a new theme.py.
- Update tests that check old strings (for example 'Tek'). Add tests for fixes 2, 3, 5, 6, 7 and 8, and for the display interpolation not changing the raw-sample measurements.
- Run `python -m unittest discover -s tests -v` with QT_QPA_PLATFORM=offscreen. All tests must pass.
- Render offscreen screenshots at 1280×800 and 1440×940 with a 500 Hz sine on CH1 and a square wave on CH2. Check for clipping, overlaps and readable contrast, then fix anything you find.
- Update README.md: the new layout, the lavender theme, the interpolation and acquisition modes, and the fact that exports always contain raw samples. Keep it short.
- At the end, give a short changelog listing each fix.
