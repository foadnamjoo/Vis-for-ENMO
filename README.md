# MotionPI System Visualization

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

##  Limitations & Future Work
- Large CSVs may require **chunked pre-filtering**.  
- Timestamp inconsistencies handled by fallback parsers.  
- Optional extensions:
  - anomaly detection,  
  - drag-and-drop CSV loader.  

---

## Timeline
- **Now**: working prototype with all layers + control panel.  
- **Milestone (Oct 21)**: refined prototype + process book update.  
- **Final**: polished system, screencast, documentation.  
