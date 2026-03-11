# Vis-for-ENMO

Interactive visualization for ENMO activity, PA-EMA triggers, and device/system monitoring from mobile-health studies.

![Interactive plot demo](assets/demo.gif)

*Screen recording: legend toggles, pan/zoom, control panel, and layer visibility.*

---

## Why This Matters

Research using wearables (Apple Watch, research wristbands, etc.) to study physical activity relies on **activity detection** and **threshold-based triggers** like PA-EMA. Sensors produce dense time-series data (accelerometer → ENMO); rules such as *"ENMO ≥ 0.1006 for ≥70% of a 7‑minute window"* decide when to prompt participants with surveys. Getting these thresholds right is critical: too strict and you miss real activity; too loose and you trigger during sedentary periods, harming data quality and participant burden.

An **interactive visualization** is essential because researchers must:

- **Verify** that PA-EMA triggers fire during real activity bouts (not noise or artifacts).
- **Tune thresholds** (ENMO level, window length, fraction) by inspecting how they behave over days and participants.
- **Diagnose failures**—battery drain, wristband disconnects, storage issues—that explain gaps or missed triggers.
- **Share findings** with non-technical collaborators (clinicians, epidemiologists) who need to explore data without writing code.

Static plots cannot support zooming into suspicious regions, toggling layers, or correlating events. This tool fills that gap for mobile-health studies where validation and interpretability are non-negotiable.

---

## Project Goal
This project builds an **interactive visualization tool** to integrate multiple data streams from a mobile-health study:

- **ENMO activity** (accelerometer, converted to 1 Hz).
- **PA-EMA survey triggers**.
- **System logs** (surveys, toggles, disconnections).
- **Device reliability metrics** (battery, storage).

The tool helps research collaborators quickly:

- Detect activity bouts and verify PA-EMA triggers.  
- Diagnose system reliability issues (battery, storage, disconnections).  
- Align participant events (surveys, toggles) with activity data.  

---

##  Audience
- **Primary users**: 20 research collaborators (Huntsman Cancer Institute + CS group).  
- **Backgrounds**: Medicine, public health, geography, computer science.  
- **Challenge**: Provide clarity for non-CS users handling **complex, multi-stream data**.  

---

##  Data
- **ENMO**: wristband accelerometer, 1 Hz time series.  
- **Events (logs)**: app/system events such as survey triggers, data collection on/off, low battery.  
- **Battery & Storage**: phone and wristband levels.  
- **Timestamps**: localized to `America/Denver`.  

---

## Tasks Supported
- **Activity detection**: identify bouts where ENMO ≥ 0.1006 for ≥70% of a 7-min window.  
- **Survey alignment**: check whether PA-EMA triggers occurred during valid activity.  
- **Reliability diagnosis**: spot gaps explained by low battery, storage issues, or disconnections.  
- **Contextual interpretation**: distinguish day vs. night activity patterns.  

---

##  Visualization Design
- **Layers**
  - ENMO points (blue) + 7-min moving average (red) + threshold (gray).  
  - PA-EMA triggers: green vertical markers + shaded pre-window.  
  - Events: stable color/symbol encoding (triangle, star, hexagon, etc.), with de-duplication.  
  - Battery/Storage: plotted on secondary Y-axis (purple/orange).  
  - Day/Night shading: yellow/blue background blocks with ☀️ / 🌙 icons.  

- **Compact control panel**
  - Pan/zoom + quick range presets.  
  - PNG export.  
  - Y-axis presets.  
  - Per-layer toggles.  

- **Design principles**
  - *Focus + context*: start with ENMO only, allow toggling other layers.  
  - *Accessibility*: color-blind-friendly palettes + redundant symbols.  
  - *Performance*: downsampling ≤250k points, WebGL scatter.  
  - *Self-sufficient*: built-in context text + legend explains encodings.  

---

##  Evaluation
- **Task success**: confirm PA-EMA detection matches rules; confirm reliability overlays explain failures.  
- **Usability**: collaborators answer questions with ≤3 interactions.  
- **Performance**: smooth interaction with 100k+ points.  
- **Design review**: principles applied (legibility, minimal clutter, interactive filtering).  

---

## Limitations & Future Work
- Large CSVs may require **chunked pre-filtering**.
- Timestamp inconsistencies handled by fallback parsers.
- Optional extensions: anomaly detection, drag-and-drop CSV loader.

---

## Getting Started

**Requirements:** Python 3.8+, pandas, numpy, plotly

```bash
pip install pandas numpy plotly
python motionpi_viz.py
```

This generates `src/index.html`. Open it in a browser to view the interactive visualization.

**Repository structure:**
- `motionpi_viz.py` — Python script to process data and generate HTML
- `src/index.html` — Interactive visualization (generated)
- `data_sample/` — Sample CSV files (ENMO, logs, battery)
- `*.pdf` — Proposal and process book documents

---

**Author:** Foad Namjoo | [foad.namjoo@utah.edu](mailto:foad.namjoo@utah.edu)
