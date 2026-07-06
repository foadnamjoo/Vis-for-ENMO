# Vis-for-ENMO

Interactive visual verification of ENMO-based physical-activity detection for **MotionPI**, a smartphone + BLE-wristband health-sensing platform ([EAI SmartSP 2025](https://arxiv.org/abs/2510.19938)). One layered WebGL timeline unifies the accelerometry-derived activity signal with the survey-trigger rule, app events, and device health — so a researcher can see *why* a trigger fired, or why it didn't.

<p align="center">
  <img src="assets/demo.gif" width="800" alt="Interactive plot demo">
</p>

*Demo: layer toggles, pan/zoom, control panel, day/night shading. 15 days of one participant's data (~213k ENMO points).*

## The problem

Free-living studies prompt participants with PA-EMA surveys when a rule detects physical activity — here: **ENMO ≥ 0.1006 g for ≥70% of samples in a centered 7-minute window**, evaluated at the auto-detected sampling cadence (~6 s in the demo, so ~70 samples per window), with contiguous qualifying windows merged into one episode and a 7-minute cooldown between accepted triggers (`motionpi_viz.py`). Threshold rules like this are unforgiving to tune: too strict and real activity is missed, too loose and participants get prompted while sitting still. Verifying the rule's behavior requires seeing the signal, the rule, the triggers, and the device context *on the same time axis* — which static plots and spreadsheet checks can't do.

## What this tool does

A Python pipeline (pandas/numpy/plotly) reads study CSV exports and generates a single interactive HTML page with all data embedded (Plotly.js itself loads from CDN):

**Time-series hygiene before anything is drawn**
- Auto-detects epoch units (seconds vs. milliseconds) and converts timestamps to the study's local timezone
- Auto-detects sampling cadence from the median inter-sample interval; collapses duplicate timestamps by mean; resamples to the detected cadence
- Canonicalizes wristband MAC addresses; the optional wristband-side file is matched by participant ID with format tolerance (full string or digits-only)
- De-duplicates repeated log events within 30-second bursts; event colors are stable across runs (hash-based)

**The layered timeline**
- ENMO point cloud (WebGL scatter, 213k points in the demo) with a 7-min centered moving average and the 0.1006 g threshold line
- Detected triggers drawn as onset markers with the preceding 7 minutes shaded (detection itself uses a centered window). The bundled demo period contains no rule-passing episodes — itself a useful negative result: the rule stays quiet through two sedentary weeks
- App/system events (surveys, toggles, disconnects) as distinct symbols on an overlay axis, with per-event tooltips
- Phone/wristband battery and storage on a secondary axis; day/night background shading
- Overview + detail via a range slider; the page opens with just the ENMO cloud, threshold line, and day/night context — moving average, triggers, events, and battery layers toggle on as needed (focus + context)

**A hand-written control panel** (vanilla JS injected into the Plotly export): draggable and collapsible, with pan/box-zoom modes, step-pan and zoom buttons, Y-axis presets, a date-time range picker with quick chips (1h–1m), per-layer checkbox lists kept in sync with the plot both ways, day/night toggle, and PNG export.

Plot rendering is decimated to ≤250k points for drawing only — detection always runs on the full series.

## Data and privacy

The pipeline expects local CSV exports (per-sample ENMO, event logs, battery/storage; optionally a wristband-side processed file). **Sample participant data is not distributed with this repository** — regenerating the page requires access to a study export. The committed `src/index.html` is a pre-generated demo (open it in a browser; Plotly.js loads from CDN, so it needs internet).

## Honest limitations

- Detection constants (threshold, window, fraction, cooldown) are module-level constants edited in the script — inspection and re-generation, not in-browser tuning
- One participant per generated page; no multi-participant comparison view
- Batch tool, not real-time: new data means re-running the script
- Feedback so far is informal beta testing with study collaborators, not a formal user study

## Getting started

```bash
pip install pandas numpy plotly
# point the three *_path constants at the top of motionpi_viz.py to your CSV export
# (the optional wristband-side file is looked up next to the ENMO CSV; see line ~111)
python motionpi_viz.py     # writes src/index.html
```

Or just open the pre-generated `src/index.html` for the demo.

## Context

Built solo as the final project for CS-6630 Visualization for Data Science (University of Utah, Fall 2025), motivated by a real need in the MotionPI study — the design iterations and evaluation are documented in the included proposal and process-book PDFs. The MotionPI platform itself (wristband firmware, collection app, backend) is the work of a research group across the University of Utah and Ohio State ([platform paper](https://arxiv.org/abs/2510.19938)); a companion tool for study-compliance monitoring lives at [motionpi-behavior-monitoring](https://github.com/foadnamjoo/motionpi-behavior-monitoring).

**Author:** Foad Namjoo · [foad.namjoo@utah.edu](mailto:foad.namjoo@utah.edu) · MIT License
