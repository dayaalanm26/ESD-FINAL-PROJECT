# Lavender DSO implementation plan

Goal: implement the supplied redesign while retaining hardware protocol and capture behavior.
Architecture: scope_app owns controls and views; theme.py owns palette; display_processing.py owns acquisition processing and display-only interpolation. Capture remains the raw calibrated record.
Spec: docs/REDESIGN_SPEC.md

1. Add regression tests for selected menu, run state, AC reference, probe scaling, cursors, empty measurements and display isolation. Run against baseline.
2. Replace layout and theme; keep existing transport/profiles/export features. Add acquisition processor with raw record preserved and a shared capture AC reference.
3. Test record compatibility, processing reset, irregular time fallback and export isolation. Run the entire unittest suite offscreen.
4. Render fixture screenshots at 1280×800, 1440×940 and 1920×1080; inspect controls, text and bounds. Update launchers and concise documentation; package all files.

Review focus: empty/short records; irregular timing; metadata changes; stopped display changes; no hardware connected. Synthetic fixtures are test-only, never a runtime signal source.
