import math
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from pathlib import Path
from datetime import datetime, timedelta
import re
import hashlib  # for stable, order-independent event colors
#working version



csv_path         = "data_sample/209C7.csv"        # ENMO CSV
logs_csv_path    = "data_sample/209Logs.csv"      # events CSV
battery_csv_path = "data_sample/209Battery.csv"   # battery CSV
html_out         = Path("src/index.html")




time_col  = "saltLakeCityTime"          # fallback time column if 'timestamp' not present
enmo_col  = "floatingPointValue"
pid_col   = "participantID"
mac_col   = "wristbandMAC"
threshold = 0.1006                       # ENMO threshold

LOCAL_TZ = "America/Denver"              # show everything in Salt Lake City time

# --- PAEMA PARAMETERS ---
PAEMA_WINDOW_MINUTES     = 7     # default matches your current 7-minute check
PAEMA_FRACTION_REQUIRED  = 0.7  # default matches your current 70% requirement

# Day/Night schedule (local time)
DAY_START_HHMM   = (7, 30)   # 7:30 AM
DAY_END_HHMM     = (21, 30)  # 9:30 PM
DAY_FILL_RGBA    = "rgba(255,247,188,0.16)"  # soft daylight tint
NIGHT_FILL_RGBA  = "rgba(40,55,71,0.15)"     # lighter night tint

# >>> Battery: plot ONLY the wristband below <<<
TARGET_WRISTBAND_MAC_FOR_BATTERY = "C1:15:BF:A7:A8:C7"  # exact MAC you asked for

# Plot performance cap (only affects what we draw, not calculations)
MAX_PLOTTED_POINTS = 250_000

# Remove selection + built-in pan/zoom/screenshot from the modebar (top-right)
PLOT_CONFIG = {
    "displaylogo": False,
    "modeBarButtonsToRemove": [
        "lasso2d", "select2d",
        "zoom2d", "pan2d", "zoomIn2d", "zoomOut2d",
        "autoScale2d", "resetScale2d",
        "toImage"
    ],
}

# ----------------------- Helpers -----------------------
def _canon_mac(x: str) -> str:
    """Uppercase hex, remove separators; returns '' if not a valid MAC."""
    if not isinstance(x, str):
        x = str(x)
    hex_only = "".join(ch for ch in x if ch.lower() in "0123456789abcdef")
    return hex_only.upper() if len(hex_only) == 12 else ""

def _pick(colnames, options):
    for o in options:
        if o in colnames: return o
    lower = {c.lower(): c for c in colnames}
    for o in options:
        if o.lower() in lower: return lower[o.lower()]
    return None

def safe_str(s: str) -> str:
    return str(s).strip().replace(":", "-").replace(" ", "_").replace("/", "-")

def _parse_unix_to_local(series: pd.Series, tz: str = LOCAL_TZ) -> pd.Series:
    """
    Parse a 'timestamp' column that may be in seconds OR milliseconds since epoch,
    localize to UTC, convert to `tz`, and return tz-naive localized datetimes.
    """
    s = pd.to_numeric(series, errors="coerce").dropna()
    if s.empty:
        return pd.to_datetime(series, errors="coerce")  # will likely be NaT

    # Heuristic: >= 10^12 -> ms; otherwise try seconds
    med = s.median()
    unit = "ms" if med >= 1_000_000_000_000 else "s"

    dt = pd.to_datetime(series, unit=unit, errors="coerce", utc=True)
    # Convert to local tz then drop tzinfo (so Plotly behaves nicely)
    dt_local = dt.dt.tz_convert(tz).dt.tz_localize(None)
    return dt_local

def _parse_time_any(df: pd.DataFrame, prefer_timestamp: bool = True, fallback_cols=None) -> pd.Series:
    """
    Prefer a 'timestamp' Unix column; otherwise try provided fallback columns (strings).
    Always return tz-naive local (America/Denver) times.
    """
    fallback_cols = fallback_cols or []
    if prefer_timestamp:
        ts_col = _pick(df.columns, ["timestamp", "Timestamp", "timeStamp"])
        if ts_col is not None:
            return _parse_unix_to_local(df[ts_col])
    # Fallback to known string time column(s)
    for c in [time_col] + list(fallback_cols):
        if c in df.columns:
            dt = pd.to_datetime(df[c], errors="coerce")
            # Assume these strings are already local; leave as naive.
            return dt
    return pd.Series(pd.NaT, index=df.index)

# ================= ENMO MAIN SERIES (20493.csv) =================
csv_path = Path(csv_path)
wristband_csv_path = csv_path.parent / "209_4BF32K_processed_Left.csv"

df = pd.read_csv(csv_path)

# Parse time from Unix timestamp (preferred); fallback to existing time column
df["_time"] = _parse_time_any(df, prefer_timestamp=True)
# Parse ENMO
df["_enmo"] = pd.to_numeric(df[enmo_col], errors="coerce")
df = df.dropna(subset=["_time", "_enmo"]).sort_values("_time").reset_index(drop=True)

# Metadata
participant_id = (
    df[pid_col].dropna().iloc[0] if pid_col in df.columns and not df[pid_col].dropna().empty else "Unknown"
)
wristband_mac = (
    df[mac_col].dropna().iloc[0] if mac_col in df.columns and not df[mac_col].dropna().empty else "Unknown"
)
TARGET_MAC_CANON = _canon_mac(wristband_mac)  # used for event filtering

TARGET_BATT_MAC_CANON = _canon_mac(TARGET_WRISTBAND_MAC_FOR_BATTERY) or TARGET_MAC_CANON

# -------- Clean duplicates & detect cadence --------
df = (
    df.set_index("_time")
      .groupby(level=0)["_enmo"].mean()
      .to_frame()
      .sort_index()
)

if len(df.index) >= 3:
    med_step = df.index.to_series().diff().dropna().median()
else:
    med_step = pd.Timedelta(seconds=5)
step_s = max(1, int(round(med_step.total_seconds())))
freq = f"{step_s}s"

g = df["_enmo"].resample(freq).mean()

# -------- 7-minute moving average --------
ma7 = g.rolling("7min", center=True, min_periods=1).mean()



# ================= WRISTBAND ENMO (device file) =================
wrist_g = pd.Series(dtype=float)
wrist_g_plot = None
if Path(wristband_csv_path).exists():
    try:
        # Auto-detect delimiter and read
        wdf = pd.read_csv(wristband_csv_path, engine="python", sep=None)

        # --- Flexible participant matching: accept "motionpi209" or "209"
        wb_pid_str = str(participant_id)
        wb_pid_digits = re.sub(r"\D", "", wb_pid_str)  # "motionpi209" -> "209"
        if "id" in wdf.columns:
            ids_str = wdf["id"].astype(str).str.strip()
            # keep rows that match either the full string or the digits-only version
            candidates = set(x for x in [wb_pid_str, wb_pid_digits] if x)
            if candidates:
                before = len(wdf)
                wdf = wdf[ids_str.isin(candidates)]
                print(f"[Wrist] Kept {len(wdf)}/{before} rows after ID match {candidates}")
        else:
            print("[Wrist] No 'id' column in wristband CSV; skipping participant filter")

        # --- Parse local Salt Lake time like "20may2025 20:09:35"
        # Try strict format first; fall back to general parser for any odd rows
        t_raw = wdf["Time"].astype(str).str.strip().str.replace(r"\s+", " ", regex=True)
        t1 = pd.to_datetime(t_raw, format="%d%b%Y %H:%M:%S", errors="coerce")
        if t1.isna().any():
            t1_fallback = pd.to_datetime(t_raw, errors="coerce")
            # prefer strict when it worked, fill others from fallback
            t1 = t1.fillna(t1_fallback)

        # ENMO from device
        enmo_w = pd.to_numeric(wdf["enmo_device"], errors="coerce")

        wdf2 = pd.DataFrame({"_t": t1, "_enmo_w": enmo_w}).dropna(subset=["_t", "_enmo_w"])
        print(f"[Wrist] After time/ENMO cleaning: {len(wdf2)} rows")

        if not wdf2.empty:
            wdf2 = (
                wdf2.set_index("_t")
                    .sort_index()
                    .groupby(level=0)["_enmo_w"].mean()
                    .to_frame()
            )

            # Resample to match main cadence (e.g., "2s")
            wrist_g = wdf2["_enmo_w"].resample(freq).mean()

            # Plot-only downsample (same strategy as main ENMO)
            Nw = len(wrist_g)
            stride_w = max(1, int(np.ceil(Nw / MAX_PLOTTED_POINTS))) if Nw > 0 else 1
            wrist_g_plot = wrist_g.iloc[::stride_w]

            # Some quick diagnostics
            if len(wrist_g) > 0:
                print(f"[Wrist] Time span: {wrist_g.index.min()}  →  {wrist_g.index.max()}")
                print(f"[Wrist] Samples: {len(wrist_g)} (plotting {len(wrist_g_plot)})")
        else:
            print("[Wrist] Parsed dataframe is empty after cleaning; nothing to plot.")
    except Exception as e:
        print(f"[Wrist] Could not load wristband CSV: {e}")
else:
    print(f"[Wrist] File not found: {wristband_csv_path}")



# -------- Summaries --------
above = (g >= threshold).astype("float")
n_exp = int(round(PAEMA_WINDOW_MINUTES * 60 / step_s))
min_periods = max(1, math.ceil(PAEMA_FRACTION_REQUIRED * n_exp))
prop7 = above.rolling(f"{PAEMA_WINDOW_MINUTES}min", center=True, min_periods=min_periods).mean()
passes70 = (prop7 >= PAEMA_FRACTION_REQUIRED)

print(f"Detected cadence: every {step_s}s (freq='{freq}')")
print(f"Expected samples in 7 min: ~{n_exp} | min_periods for rule: {min_periods}")
print(f"Sample-wise fraction ≥ {threshold}: {np.nanmean(above.values):.2%}")
print(f"Fraction of 7-min windows with ≥70% of samples ≥ {threshold}: {np.nanmean(passes70.values):.2%}")

# -------- Title metadata --------
start_dt = g.index.min()
end_dt   = g.index.max()
start_str = start_dt.strftime("%Y-%m-%d")
end_str   = end_dt.strftime("%Y-%m-%d")

participant_id_safe = safe_str(participant_id)
wristband_mac_safe  = safe_str(wristband_mac)

# --- Timestamp for filename ---
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
html_out = csv_path.parent / f"enmo_{participant_id_safe}_{wristband_mac_safe}_{timestamp}.html"

# -------- Plot-only downsampling --------
N = len(g)
stride = max(1, int(np.ceil(N / MAX_PLOTTED_POINTS))) if N > 0 else 1
g_plot   = g.iloc[::stride]
ma7_plot = ma7.iloc[::stride]

# ================== PAEMA triggers with cooldown ==================
p = passes70.fillna(False)
episodes = []
if len(p) > 0:
    grp = (p != p.shift()).cumsum()
    for _, seg in p.groupby(grp):
        if seg.iloc[0]:
            episodes.append((seg.index[0], seg.index[-1]))

cooldown = pd.Timedelta(minutes=PAEMA_WINDOW_MINUTES)
accepted = []
last_start = None
for s, e in episodes:
    if (last_start is None) or (s >= last_start + cooldown):
        accepted.append((s, e))
        last_start = s

accepted_mask = pd.Series(False, index=g.index)
for s, e in accepted:
    accepted_mask.loc[(accepted_mask.index >= s) & (accepted_mask.index <= e)] = True

# ----------------------- Load Battery CSV (optional) -----------------------
battery_df = None
side = None  # 'left' or 'right' of TARGET_BATT_MAC_CANON
if Path(battery_csv_path).exists():
    bdf = pd.read_csv(battery_csv_path)

    # Filter same participant (if present)
    if "participantID" in bdf.columns and participant_id not in (None, "Unknown"):
        bdf = bdf[bdf["participantID"].astype(str) == str(participant_id)]

    # Parse Unix timestamp (ms or s) -> local Salt Lake City time
    ts_col_b = _pick(bdf.columns, ["timestamp", "Timestamp", "timeStamp"])
    if ts_col_b is not None:
        bdf["_bt"] = _parse_unix_to_local(bdf[ts_col_b])
    else:
        # Fallback: any time-like column
        timecand = [c for c in bdf.columns if "time" in c.lower()]
        if timecand:
            bdf["_bt"] = pd.to_datetime(bdf[timecand[0]], errors="coerce")
        else:
            bdf["_bt"] = pd.NaT

    bdf = bdf.dropna(subset=["_bt"]).sort_values("_bt")

    # Keep only battery rows within the ENMO time span
    if len(g) > 0:
        tmin, tmax = g.index.min(), g.index.max()
        bdf = bdf[(bdf["_bt"] >= tmin) & (bdf["_bt"] <= tmax)]

    # Canonicalize MACs present in battery CSV
    for col in ["leftwristbandMAC", "rightwristbandMAC"]:
        if col in bdf.columns:
            bdf[col + "_canon"] = bdf[col].astype(str).map(_canon_mac)
        else:
            bdf[col + "_canon"] = ""

    # Decide matching side for the *requested* wristband MAC
    left_match  = (bdf.get("leftwristbandMAC_canon",  "") == TARGET_BATT_MAC_CANON).sum()
    right_match = (bdf.get("rightwristbandMAC_canon", "") == TARGET_BATT_MAC_CANON).sum()
    if left_match > 0 or right_match > 0:
        side = "left" if left_match >= right_match else "right"
    else:
        side = None  # cannot find requested MAC on either side

    # Resample for responsiveness (≤ ~50k points)
    if not bdf.empty:
        bdf = bdf.set_index("_bt")
        # Keep only the columns we might plot
        keep_cols = [c for c in [
            "phoneBatteryLevel",
            "leftwristbandBatteryLevel", "rightwristbandBatteryLevel",
            "leftwristbandStorageUsedPercent", "rightwristbandStorageUsedPercent"
        ] if c in bdf.columns]
        if keep_cols:
            battery_df = bdf[keep_cols].copy()
            target_rule = f"{max(step_s, 30)}s"  # no finer than 30s
            agg = {c: "mean" for c in keep_cols}
            battery_df = battery_df.resample(target_rule).agg(agg).dropna(how="all")
            if len(battery_df) > 50_000:
                step = int(np.ceil(len(battery_df) / 50_000))
                battery_df = battery_df.iloc[::step]

# -------------------- ColorBrewer palettes & fixed colors --------------------
# Color-blind-friendly sets from ColorBrewer
CBR_SET2  = ["#66c2a5","#fc8d62","#8da0cb","#e78ac3","#a6d854","#ffd92f","#e5c494","#b3b3b3"]
CBR_DARK2 = ["#1b9e77","#d95f02","#7570b3","#e7298a","#66a61e","#e6ab02","#a6761d","#666666"]
CBR_PAIRED= ["#a6cee3","#1f78b4","#b2df8a","#33a02c","#fb9a99","#e31a1c",
             "#fdbf6f","#ff7f00","#cab2d6","#6a3d9a","#ffff99","#b15928"]

EVENT_COLOR_POOL = CBR_SET2 + CBR_DARK2 + CBR_PAIRED  # 28 distinct colors

def color_for_event(name: str) -> str:
    """Stable, order-independent color choice for an event name."""
    h = int(hashlib.md5(str(name).encode("utf-8")).hexdigest(), 16)
    return EVENT_COLOR_POOL[h % len(EVENT_COLOR_POOL)]

# Fixed colors for main layers
COLORS = {
    "ENMO": "#1f78b4",         # Paired blue
    "MA7": "#e31a1c",          # Paired red
    "THRESHOLD": "#737373",    # neutral gray
    "PAEMA": "#33a02c",        # Paired green
    "PHONE_BATT": "#6a3d9a",   # Paired purple
    "WRIST_BATT": "#e7298a",   # Dark2 magenta (changed)
    "WRIST_STORAGE": "#ff7f00",# Paired orange  <-- comma here
    "ENMO_DEVICE": "#b15928",  # Paired brown — wristband ENMO points
}


# -------- Plot --------
fig = go.Figure()

# ENMO points (fixed color)
fig.add_trace(go.Scattergl(
    x=g_plot.index, y=g_plot.values,
    mode="markers",
    marker=dict(size=5, opacity=0.75, color=COLORS["ENMO"]),
    name="ENMO (resampled)",
    hoverinfo="skip",
    showlegend=True,
    yaxis="y1"
))


# Wristband ENMO points (toggleable layer)
if wrist_g_plot is not None and len(wrist_g_plot) > 0:
    fig.add_trace(go.Scattergl(
        x=wrist_g_plot.index, y=wrist_g_plot.values,
        mode="markers",
        marker=dict(size=4, opacity=0.7, color=COLORS["ENMO_DEVICE"]),
        name="ENMO (wristband file)",
        hoverinfo="skip",
        showlegend=True,
        yaxis="y1"
    ))


# 7-min moving average line (fixed color)
fig.add_trace(go.Scatter(
    x=ma7_plot.index, y=ma7_plot.values,
    mode="lines",
    line=dict(width=2, color=COLORS["MA7"]),
    name="7-min moving average",
    showlegend=True,
    yaxis="y1"
))

# Threshold as a trace (NOT in legend) — fixed gray
if len(g_plot) > 1:
    fig.add_trace(go.Scatter(
        x=[g_plot.index.min(), g_plot.index.max()],
        y=[threshold, threshold],
        mode="lines",
        line=dict(width=2, dash="dot", color="rgba(115,115,115,0.85)"),
        name="__threshold__",
        hoverinfo="skip",
        showlegend=False,
        visible=True,
        yaxis="y1"
    ))

# --- Simplified PAEMA visuals: vertical line + previous 7 min band + top arrow only ---
TRIGGER_COLOR = COLORS["PAEMA"]  # fixed PAEMA color
WINDOW_MINUTES = PAEMA_WINDOW_MINUTES

onsets = [s for s, _ in accepted]
for t in onsets:
    t0 = t - pd.Timedelta(minutes=WINDOW_MINUTES)

    # (1) Last 7 minutes band (full-height)
    fig.add_shape(
        type="rect", xref="x", yref="paper",
        x0=t0, x1=t, y0=0, y1=1,
        fillcolor="rgba(46,204,113,0.12)",
        line=dict(width=0),
        layer="below",
    )

    # (2) Vertical line at trigger
    fig.add_shape(
        type="line", xref="x", yref="paper",
        x0=t, x1=t, y0=0, y1=1,
        line=dict(color=TRIGGER_COLOR, width=2)
    )

    # (3) Arrow above the plot, pointing to the trigger time
    fig.add_annotation(
        x=t, xref="x",
        y=1.0, yref="paper",
        text="",
        showarrow=True,
        arrowhead=3, arrowsize=1.2, arrowwidth=2.2, arrowcolor=TRIGGER_COLOR,
        ax=0, ay=26,
        opacity=0.95
    )

# ---------- PAEMA LEGEND ITEM (dummy trace) ----------
if len(g_plot) > 0:
    fig.add_trace(go.Scatter(
        x=[g_plot.index[0], g_plot.index[0]],
        y=[0, 0],
        mode="lines",
        line=dict(width=2, color=TRIGGER_COLOR),
        name="PAEMA",
        hoverinfo="skip",
        showlegend=True,
        yaxis="y1"
    ))

# === Day/Night background shading + icons (☀️ / 🌙) ===
if pd.notna(start_dt) and pd.notna(end_dt):
    # Normalize to whole days to cover full span
    day_ptr = pd.to_datetime(start_dt.date())
    end_day = pd.to_datetime(end_dt.date())
    while day_ptr <= end_day:
        ds = day_ptr + pd.Timedelta(hours=DAY_START_HHMM[0], minutes=DAY_START_HHMM[1])
        de = day_ptr + pd.Timedelta(hours=DAY_END_HHMM[0], minutes=DAY_END_HHMM[1])
        next_morning = (day_ptr + pd.Timedelta(days=1)) + pd.Timedelta(hours=DAY_START_HHMM[0], minutes=DAY_START_HHMM[1])

        # Clip to data span
        span0, span1 = start_dt, end_dt

        # Day block
        x0 = max(ds, span0); x1 = min(de, span1)
        if x1 > x0:
            fig.add_shape(
                type="rect", xref="x", yref="paper",
                x0=x0, x1=x1, y0=0, y1=1,
                fillcolor=DAY_FILL_RGBA, line=dict(width=0),
                layer="below"
            )
            # Sun icon near top
            fig.add_annotation(
                x=x0 + (x1 - x0) / 2, xref="x",
                y=0.965, yref="paper",
                text="☀️", showarrow=False, font=dict(size=10), opacity=0.5
            )

        # Night block (from 21:30 -> next 07:30)
        n0 = max(de, span0); n1 = min(next_morning, span1)
        if n1 > n0:
            fig.add_shape(
                type="rect", xref="x", yref="paper",
                x0=n0, x1=n1, y0=0, y1=1,
                fillcolor=NIGHT_FILL_RGBA, line=dict(width=0),
                layer="below"
            )
            # Moon icon near top
            fig.add_annotation(
                x=n0 + (n1 - n0) / 2, xref="x",
                y=0.965, yref="paper",
                text="🌙", showarrow=False, font=dict(size=16), opacity=0.95
            )

        day_ptr = day_ptr + pd.Timedelta(days=1)

# === Events overlay (overlay axis y3 so markers stay near top regardless of Y scale) ===
EVENT_MARKER_Y = 0.98   # 98% up the plot area
DEDUP_WINDOW_SEC = 30
EVENTS_LEGEND_GROUP = "Events"

# ---- EXACT symbol mapping for your listed events ----
EXACT_SYMBOLS = {
    "Data Collection Disabled": "x",
    "Data Collection Enabled": "star",
    "Dominant hand change to Left": "triangle-left",
    "PA_EMA_new-notification": "hexagon",
    "PA_EMA_triggered-by-wristband": "hexagon2",
    "afternoon_survey_trigger": "triangle-up",
    "connect-wristband-to-app": "square",
    "disconnect-wristband-to-app": "square-open",
    "evening_survey_trigger": "triangle-down",
    "low_battery_wristband_E9:E6:9C:CF:EE:93": "hourglass",
    "morning_survey_trigger": "triangle-right",
}
LOW_BATTERY_PREFIX = "low_battery"  # fallback for any low_battery* names

logs_path = Path(logs_csv_path)
if logs_path.exists():
    try:
        evdf = pd.read_csv(logs_path)

        name_col = _pick(evdf.columns, ["eventName","event_name","EventName","event"])
        pid_col_ev  = _pick(evdf.columns, ["participantID","participant_id","ParticipantID"])
        mac_col_ev  = _pick(evdf.columns, ["wristbandMAC","mac","WristbandMAC"])

        # ALWAYS prefer timestamp for logs
        ts_col_ev = _pick(evdf.columns, ["timestamp", "Timestamp", "timeStamp"])
        if ts_col_ev is not None:
            evdf["_ev_time"] = _parse_unix_to_local(evdf[ts_col_ev])
        else:
            # Fallback to any readable time column if present
            time_col_ev = _pick(evdf.columns, ["saltLakeCityTime","saltlaketime","SaltLakeCityTime","SaltLakeTime"])
            if time_col_ev is not None:
                evdf["_ev_time"] = pd.to_datetime(evdf[time_col_ev], errors="coerce")
            else:
                evdf["_ev_time"] = pd.NaT

        evdf["_ev_name"] = evdf[name_col].astype(str).str.strip() if name_col is not None else "event"

        # --------------------- Keep only events for the same wristband as ENMO (or MAC-less)
        mac_regex = r'([0-9A-Fa-f]{2}(?:[:\-][0-9A-Fa-f]{2}){5}|[0-9A-Fa-f]{12})'
        if (mac_col_ev is not None) and (mac_col_ev in evdf.columns):
            evdf["_ev_mac_raw"] = evdf[mac_col_ev].astype(str)
        else:
            evdf["_ev_mac_raw"] = evdf["_ev_name"].str.extract(mac_regex, expand=False).fillna("")
        evdf["_ev_mac"] = evdf["_ev_mac_raw"].map(_canon_mac)
        if TARGET_MAC_CANON:
            evdf = evdf[(evdf["_ev_mac"] == "") | (evdf["_ev_mac"] == TARGET_MAC_CANON)]
        # ---------------------------------------------------------------------------------------

        # Filter to same participant (strict)
        if (pid_col_ev is not None) and pd.notna(participant_id):
            evdf = evdf[evdf[pid_col_ev].astype(str) == str(participant_id)]

        evdf = evdf.dropna(subset=["_ev_time"]).sort_values("_ev_time")

        # Only keep events within ENMO data span
        if len(g) > 0:
            tmin, tmax = g.index.min(), g.index.max()
            evdf = evdf[(evdf["_ev_time"] >= tmin) & (evdf["_ev_time"] <= tmax)]

        if not evdf.empty and name_col is not None:
            unique_events = sorted(evdf["_ev_name"].unique().tolist())

            for ev_name in unique_events:
                sub = evdf[evdf["_ev_name"] == ev_name].copy()
                if sub.empty:
                    continue

                # De-duplicate bursts within MAC if available, else by name
                if mac_col_ev is not None and mac_col_ev in sub.columns:
                    sub["_key"] = sub["_ev_name"] + "||" + sub[mac_col_ev].astype(str)
                else:
                    sub["_key"] = sub["_ev_name"]
                sub = sub.sort_values(["_key","_ev_time"])
                sub["_delta"] = sub.groupby("_key")["_ev_time"].diff().dt.total_seconds()
                if DEDUP_WINDOW_SEC > 0:
                    sub = sub[(sub["_delta"].isna()) | (sub["_delta"] > DEDUP_WINDOW_SEC)]

                if sub.empty:
                    continue

                # Hover
                when = sub["_ev_time"].dt.strftime("%Y-%m-%d %H:%M:%S")
                if mac_col_ev is not None and mac_col_ev in sub.columns:
                    hover = (
                        "Event: <b>" + sub["_ev_name"] + "</b><br>" +
                        "Time: " + when + "<br>" +
                        "MAC: " + sub[mac_col_ev].astype(str)
                    )
                else:
                    hover = (
                        "Event: <b>" + sub["_ev_name"] + "</b><br>" +
                        "Time: " + when
                    )

                # Color & marker symbol (stable color by name; symbol by EXACT mapping/prefix/default)
                color = color_for_event(ev_name)
                if ev_name in EXACT_SYMBOLS:
                    symbol = EXACT_SYMBOLS[ev_name]
                elif ev_name.startswith(LOW_BATTERY_PREFIX):
                    symbol = "hourglass"
                else:
                    symbol = "triangle-up"  # safe default

                # Put event markers on overlay axis y3 so they stay near the top no matter the Y scale
                fig.add_trace(go.Scattergl(
                    x=sub["_ev_time"],
                    y=np.full(len(sub), EVENT_MARKER_Y),  # in [0,1] scale for y3
                    mode="markers",
                    name=ev_name,
                    marker=dict(
                        symbol=symbol,
                        size=11,
                        color=color,
                        opacity=0.98,
                        line=dict(width=0.8, color="rgba(0,0,0,0.55)")  # subtle outline for readability
                    ),
                    hovertemplate=hover + "<extra></extra>",
                    showlegend=True,
                    legendgroup=EVENTS_LEGEND_GROUP,
                    legendgrouptitle=dict(text=EVENTS_LEGEND_GROUP),
                    yaxis="y3"  # <--- overlay axis
                ))
    except Exception as e:
        print(f"[Events overlay] Could not load logs CSV: {e}")
# ========================================================================

# ---------------------- Add Battery & Storage Traces (right axis) ----------------------
BATTERY_GROUP = "Battery"

def add_line(x, y, name, dash=None, width=2, opacity=0.95, show_points=False, color=None):
    fig.add_trace(go.Scattergl(
        x=x, y=y,
        mode="lines+markers" if show_points else "lines",
        line=dict(width=width, dash=(dash or "solid"), color=color),
        marker=(dict(size=5, opacity=0.95, color=color) if show_points else None),
        name=name,
        legendgroup=BATTERY_GROUP,
        legendgrouptitle=dict(text=BATTERY_GROUP),
        opacity=opacity,
        yaxis="y2",
        hovertemplate=f"{name}: %{{y:.0f}}%<br>%{{x|%Y-%m-%d %H:%M:%S}}<extra></extra>"
    ))

if battery_df is not None and not battery_df.empty:
    # Phone battery with points + connecting line
    if "phoneBatteryLevel" in battery_df.columns:
        add_line(
            battery_df.index, battery_df["phoneBatteryLevel"],
            name="Phone Battery (%)", dash=None, width=2.2, show_points=True,
            color=COLORS["PHONE_BATT"]
        )

    # Wristband BATTERY for the requested MAC only (pick the side that matches)
    wrist_batt_col = None
    wrist_batt_name = None
    if side == "left" and "leftwristbandBatteryLevel" in battery_df.columns:
        wrist_batt_col, wrist_batt_name = "leftwristbandBatteryLevel", "Wrist Battery (%) — left"
    elif side == "right" and "rightwristbandBatteryLevel" in battery_df.columns:
        wrist_batt_col, wrist_batt_name = "rightwristbandBatteryLevel", "Wrist Battery (%) — right"

    if wrist_batt_col is not None:
        add_line(
            battery_df.index, battery_df[wrist_batt_col],
            name=wrist_batt_name, dash="solid", width=2.2, show_points=False,
            color=COLORS["WRIST_BATT"]
        )
    else:
        print(f"[Battery] Requested wristband MAC {TARGET_WRISTBAND_MAC_FOR_BATTERY} not found on left/right MAC columns; wrist battery not drawn.")

    # Wristband STORAGE for the same side (if available)
    storage_col, storage_name = None, None
    if side == "left" and "leftwristbandStorageUsedPercent" in battery_df.columns:
        storage_col, storage_name = "leftwristbandStorageUsedPercent", "Wrist Storage (%) — left"
    elif side == "right" and "rightwristbandStorageUsedPercent" in battery_df.columns:
        storage_col, storage_name = "rightwristbandStorageUsedPercent", "Wrist Storage (%) — right"

    if storage_col is not None:
        add_line(
            battery_df.index, battery_df[storage_col],
            name=storage_name, dash="dot", width=3.6, opacity=0.9, show_points=False,
            color=COLORS["WRIST_STORAGE"]
        )

# ---------- Clean Title + Subtitle ----------
title_main = f"ENMO (Salt Lake City time)"
subtitle = (
    f"Participant: <b>{participant_id}</b> &nbsp;·&nbsp; "
    f"MAC: <b>{wristband_mac}</b> &nbsp;·&nbsp; "
    f"Start: <b>{start_str}</b> &nbsp;·&nbsp; "
    f"End: <b>{end_str}</b>"
)
# ---------- Title (simpler & reliable placement) ----------
fig.update_layout(
    title_text=f"<b>ENMO (Salt Lake City time)</b><br>"
               f"<span style='font-size:12px;color:rgba(80,80,80,0.95)'>"
               f"Participant: <b>{participant_id}</b> &nbsp;·&nbsp; "
               f"MAC: <b>{wristband_mac}</b> &nbsp;·&nbsp; "
               f"Start: <b>{start_str}</b> &nbsp;·&nbsp; "
               f"End: <b>{end_str}</b></span>",
    title_x=0.5,          # center
    # leave title_y unset so Plotly chooses a safe spot
)

# Give the title a touch more breathing room
fig.update_layout(margin=dict(l=40, r=60, t=140, b=100))


# --- Axes & legend layout ---
fig.update_layout(
    xaxis=dict(title="Time (America/Denver)"),
    yaxis=dict(title="ENMO", range=[0, 0.2], autorange=False, automargin=True, domain=[0,1], anchor="x", side="left"),
    yaxis2=dict(title="Battery (%)", range=[0,100], overlaying="y", side="right"),
    # y3 overlay axis dedicated to events (0..1), hidden
    yaxis3=dict(range=[0,1], overlaying="y", side="right", showticklabels=False, ticks="", showgrid=False, zeroline=False),
    hovermode="x unified",
    margin=dict(l=40, r=60, t=115, b=100),

    legend=dict(
        title_text="Layers (click to toggle)",
        x=0.01, xanchor="left",
        y=0.99, yanchor="top",
        orientation="v",
        bgcolor="rgba(255,255,255,0.75)",
        bordercolor="rgba(0,0,0,0.15)",
        borderwidth=1,
        itemsizing="constant",
        itemclick="toggle",
        itemdoubleclick="toggleothers",
        font=dict(size=12)
    ),
    legend_title=dict(font=dict(size=12)),
    uirevision="static",
    dragmode="pan"  # sensible default
)

# Keep rangeslider
fig.update_xaxes(rangeslider=dict(visible=True, thickness=0.055))  # thinner slider (~5.5% of plot height)

# ------- MERGED, COMPACT CONTROL PANEL (with pan/zoom/screenshot + Y-scale + Batteries + Day/Night toggle) -------
POST_SCRIPT = r"""
(function(){
  var gd = document.getElementById('{plot_id}');
  if (!gd) return;

  // ---------- Styles ----------
  if (!document.getElementById('ctlpanel-style')) {
    var css = `
      .ctlpanel{
        position:absolute; z-index:14;
        min-width: 220px; max-width: 360px;
        background: color-mix(in oklab, canvasText 6%, canvas);
        border:1px solid color-mix(in oklab, canvasText 14%, transparent);
        border-radius:.6rem; backdrop-filter:blur(2px);
        box-shadow:0 2px 12px color-mix(in oklab, canvasText 12%, transparent);
        color:canvasText; font:12px/1.35 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Inter,system-ui,sans-serif;
        display:grid; grid-template-rows: min-content auto;
        opacity:.92; transition:opacity .15s ease;
      }
      .ctlpanel:hover{ opacity:1; }
      .ctlpanel__title{
        padding:.45rem .55rem; font-weight:700; user-select:none;
        display:flex; align-items:center; justify-content:space-between; gap:.5rem;
        border-bottom:1px solid color-mix(in oklab, canvasText 12%, transparent);
        background: color-mix(in oklab, canvasText 4%, canvas);
        border-top-left-radius:.6rem; border-top-right-radius:.6rem;
        cursor:move;
      }
      .ctlpanel__body{ padding:.45rem .55rem; display:grid; gap:.45rem; }
      .toolbar{ display:flex; gap:.3rem; flex-wrap:wrap; }
      .toolbtn{
        padding:.24rem .44rem; border-radius:.4rem; border:1px solid color-mix(in oklab, canvasText 14%, transparent);
        background: color-mix(in oklab, canvasText 4%, canvas); cursor:pointer; font:11.5px/1.1 inherit;
      }
      .toolbtn[aria-pressed="true"]{
        background: color-mix(in oklab, canvasText 12%, canvas);
        border-color: color-mix(in oklab, canvasText 24%, transparent);
        font-weight:700;
      }
      .section{ border:1px solid color-mix(in oklab, canvasText 10%, transparent); border-radius:.45rem; overflow:hidden; }
      .section > summary{
        padding:.4rem .5rem; font-weight:600; list-style:none; cursor:pointer;
        background: color-mix(in oklab, canvasText 3%, canvas);
      }
      .section[open] > summary{ border-bottom:1px solid color-mix(in oklab, canvasText 10%, transparent); }
      .section__body{ padding:.45rem .5rem .5rem; display:grid; gap:.45rem; }
      .row{ display:flex; align-items:center; gap:.4rem; flex-wrap:wrap; }
      .input{
        font:12px/1.2 inherit; color:inherit; min-width: 9.5rem;
        border:1px solid color-mix(in oklab, canvasText 12%, transparent);
        background: color-mix(in oklab, canvasText 4%, canvas);
        border-radius:.35rem; padding:.2rem .35rem;
      }
      .arrow{ opacity:.7; font-weight:700; }
      .chips{ display:flex; gap:.35rem; flex-wrap:wrap; }
      .chip{ border:1px solid color-mix(in oklab, canvasText 14%, transparent);
             background: color-mix(in oklab, canvasText 4%, canvas); cursor:pointer;
             border-radius:999px; padding:.2rem .55rem; font-weight:600; font-size:11.5px; }
      .list{ display:grid; gap:.25rem; max-height: 220px; overflow:auto; padding-right:.2rem; }
      .item{ display:flex; align-items:center; gap:.4rem; }
      .sw{ width:14px; height:14px; }
      .chip-color{ width:10px; height:10px; border-radius:2px; border:1px solid color-mix(in oklab, canvasText 20%, transparent); }
      .label{ white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
      .minibtn{ margin-left:.3rem; border:1px solid color-mix(in oklab, canvasText 18%, transparent); background:transparent; border-radius:.35rem; padding:.15rem .35rem; cursor:pointer; font-size:11px; }
      .section__hint{ opacity:.75; font-style:italic; font-size:11px; }
    `;
    var s=document.createElement('style'); s.id='ctlpanel-style'; s.textContent=css; document.head.appendChild(s);
  }

  // Prepare parent positioning
  var parent = gd.parentNode;
  if (getComputedStyle(parent).position === 'static') parent.style.position = 'relative';

  // ---------- Build merged panel ----------
  var panel = document.createElement('div');
  panel.className = 'ctlpanel';

  // Header (drag + collapse)
  var header = document.createElement('div');
  header.className = 'ctlpanel__title';
  var titleSpan = document.createElement('span'); titleSpan.textContent = 'Controls';
  var collapseBtn = document.createElement('button'); collapseBtn.className='minibtn'; collapseBtn.textContent='▾';
  header.append(titleSpan, collapseBtn);
  panel.appendChild(header);

  var bodyWrap = document.createElement('div');
  bodyWrap.className = 'ctlpanel__body';

  // ---- Toolbar row: Pan/Zoom + pan left/right + zoom in/out + autoscale + screenshot + Y-axis + Day/Night ----
  var toolbar = document.createElement('div'); toolbar.className='toolbar';

  function mkBtn(label, title){ var b=document.createElement('button'); b.className='toolbtn'; b.textContent=label; b.title=title||label; return b; }

  var btnPan    = mkBtn('Pan','Drag to pan');    btnPan.setAttribute('aria-pressed','true');
  var btnZoom   = mkBtn('Box','Drag to box-zoom'); btnZoom.setAttribute('aria-pressed','false');
  var btnLeft   = mkBtn('◄','Pan left');
  var btnRight  = mkBtn('►','Pan right');
  var btnOut    = mkBtn('−','Zoom out');
  var btnIn     = mkBtn('+','Zoom in');
  var btnAutoX  = mkBtn('Auto X','Autoscale X axis');
  var btnAutoY  = mkBtn('Auto Y','Autoscale Y axes');
  var btnReset  = mkBtn('Reset','Reset view & modes');
  var btnShot   = mkBtn('Shot','Save PNG screenshot');
  var btnDN     = mkBtn('DN','Toggle Day/Night shading'); btnDN.setAttribute('aria-pressed','true');

  // Y scale presets for ENMO axis
  var yLabel = mkBtn('Y','Y-scale presets'); yLabel.disabled = true; yLabel.style.opacity=.7;
  var btnY02 = mkBtn('0–0.2','Y: 0 to 0.2');
  var btnY05 = mkBtn('0–0.5','Y: 0 to 0.5');
  var btnY10 = mkBtn('0–1','Y: 0 to 1');
  var btnYAu = mkBtn('Auto','Y: autoscale');

  toolbar.append(btnPan, btnZoom, btnLeft, btnRight, btnOut, btnIn, btnAutoX, btnAutoY, btnReset, btnShot, btnDN, yLabel, btnY02, btnY05, btnY10, btnYAu);

  // ---- Visible Range section (collapsed by default) ----
  var secVR = document.createElement('details'); secVR.className='section'; secVR.open=false;
  var sumVR = document.createElement('summary'); sumVR.textContent='Visible Range';
  var bodyVR= document.createElement('div'); bodyVR.className='section__body';

  var rowInputs = document.createElement('div'); rowInputs.className = 'row';
  var inpStart = document.createElement('input'); inpStart.type='datetime-local'; inpStart.className='input'; inpStart.ariaLabel='Start date time';
  var arrow    = document.createElement('span');  arrow.className='arrow'; arrow.textContent='to';
  var inpEnd   = document.createElement('input'); inpEnd.type='datetime-local';   inpEnd.className='input';  inpEnd.ariaLabel='End date time';
  rowInputs.append(inpStart, arrow, inpEnd);

  var rowChips = document.createElement('div'); rowChips.className='chips'; rowChips.setAttribute('role','toolbar'); rowChips.ariaLabel='Quick range presets';
  [
    {label:'1h',hours:1},{label:'6h',hours:6},{label:'1d',hours:24},
    {label:'1w',hours:168},{label:'2w',hours:336},{label:'1m',hours:720},{label:'All',hours:null}
  ].forEach(function(p){
    var b=document.createElement('button'); b.className='chip'; b.type='button'; b.textContent=p.label; b.title=p.hours?('Show last '+p.label):'Show all data';
    b.addEventListener('click', function(){ applyPreset(p.hours); }); rowChips.appendChild(b);
  });

  bodyVR.append(rowInputs, rowChips); secVR.append(sumVR, bodyVR);

  // ---- Events section (collapsed by default) ----
  var secEV = document.createElement('details'); secEV.className='section'; secEV.open=false;
  var sumEV = document.createElement('summary'); sumEV.textContent='Events';
  var bodyEV= document.createElement('div'); bodyEV.className='section__body';
  var hintEV= document.createElement('div');  hintEV.className='section__hint'; hintEV.textContent='Toggle individual event types';
  var rowEV = document.createElement('div'); rowEV.className='row';
  var btnAllOnEV  = mkBtn('All on'); var btnAllOffEV = mkBtn('All off');
  rowEV.append(btnAllOnEV, btnAllOffEV);
  var listEV = document.createElement('div'); listEV.className='list';
  bodyEV.append(hintEV, rowEV, listEV); secEV.append(sumEV, bodyEV);

  // ---- Battery & Storage section (collapsed by default) ----
  var secBAT = document.createElement('details'); secBAT.className='section'; secBAT.open=false;
  var sumBAT = document.createElement('summary'); sumBAT.textContent='Battery & Storage';
  var bodyBAT= document.createElement('div'); bodyBAT.className='section__body';
  var hintBT = document.createElement('div');  hintBT.className='section__hint'; hintBT.textContent='Toggle phone battery and wrist storage lines';
  var rowBT  = document.createElement('div');  rowBT.className='row';
  var btnAllOnBT  = mkBtn('All on'); var btnAllOffBT = mkBtn('All off');
  rowBT.append(btnAllOnBT, btnAllOffBT);
  var listBT = document.createElement('div'); listBT.className='list';
  bodyBAT.append(hintBT, rowBT, listBT); secBAT.append(sumBAT, bodyBAT);

  bodyWrap.append(toolbar, secVR, secEV, secBAT);
  panel.appendChild(bodyWrap);
  parent.appendChild(panel);

  // ---------- Position logic (bottom-left; compact header-only by default) ----------
  var userMoved = false;
  function positionPanel(){
    if (userMoved) return;
    var x = 8, y = 8;
    if (gd._fullLayout && gd._fullLayout._size){
      var sz = gd._fullLayout._size;
      x = sz.l + 8;
      y = gd.offsetHeight - sz.b - panel.offsetHeight - 8;
    }
    panel.style.left = x + 'px';
    panel.style.top  = y + 'px';
  }

  // Drag to move
  var header = panel.querySelector('.ctlpanel__title');
  var collapseBtn = header.querySelector('.minibtn');
  header.addEventListener('mousedown', function(e){
    if (e.target === collapseBtn) return;
    e.preventDefault();
    var rect = panel.getBoundingClientRect();
    var pref = parent.getBoundingClientRect();
    var offsetX = e.clientX - rect.left;
    var offsetY = e.clientY - rect.top;

    function onMove(ev){
      var x = ev.clientX - pref.left - offsetX;
      var y = ev.clientY - pref.top  - offsetY;
      x = Math.max(0, Math.min(x, parent.clientWidth  - panel.offsetWidth));
      y = Math.max(0, Math.min(y, parent.clientHeight - panel.offsetHeight));
      panel.style.left = x + 'px';
      panel.style.top  = y + 'px';
    }
    function onUp(){
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
      userMoved = true;
    }
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  });

  // Collapse body by default (super compact)
  var bodyWrap = panel.querySelector('.ctlpanel__body');
  var collapsed = true;
  function syncCollapse(){
    bodyWrap.style.display = collapsed ? 'none' : 'grid';
    collapseBtn.textContent = collapsed ? '▸' : '▾';
    positionPanel();
  }
  collapseBtn.addEventListener('click', function(e){ e.stopPropagation(); collapsed = !collapsed; syncCollapse(); });
  header.addEventListener('dblclick', function(){ collapsed = !collapsed; syncCollapse(); });
  syncCollapse();

  // ---------- Utilities ----------
  function pad(n){ return (n<10 ? '0'+n : ''+n); }
  function toLocalInputValue(d){
    if(!(d instanceof Date) || isNaN(d)) return '';
    return d.getFullYear()+'-'+pad(d.getMonth()+1)+'-'+pad(d.getDate())+'T'+pad(d.getHours())+':'+pad(d.getMinutes());
  }
  function currentXRange(){
    var ax = (gd.layout && gd.layout.xaxis && gd.layout.xaxis.range) ? gd.layout.xaxis.range :
             (gd._fullLayout && gd._fullLayout.xaxis && gd._fullLayout.xaxis.range ? gd._fullLayout.xaxis.range : null);
    return ax ? [new Date(ax[0]).getTime(), new Date(ax[1]).getTime()] : null;
  }
  function syncFromPlot(){
    var r = currentXRange(); if(!r || r.length<2) return;
    var inpStart = panel.querySelector('input[type=datetime-local]:first-of-type');
    var inpEnd   = panel.querySelector('input[type=datetime-local]:last-of-type');
    if (document.activeElement !== inpStart) inpStart.value = toLocalInputValue(new Date(r[0]));
    if (document.activeElement !== inpEnd)   inpEnd.value   = toLocalInputValue(new Date(r[1]));
    requestAnimationFrame(positionPanel);
  }
  function applyInputRange(){
    var inputs = panel.querySelectorAll('input[type=datetime-local]');
    var inpStart = inputs[0], inpEnd = inputs[1];
    if(!inpStart.value || !inpEnd.value) return;
    var s = new Date(inpStart.value), e = new Date(inpEnd.value);
    if(isNaN(s) || isNaN(e)) return;
    if (s >= e){ var t=s; s=e; e=t; }
    Plotly.relayout(gd, {'xaxis.range':[s, e]});
  }
  panel.querySelectorAll('input[type=datetime-local]').forEach(function(i){
    ['change','blur'].forEach(function(ev){ i.addEventListener(ev, applyInputRange); });
    i.addEventListener('keydown', function(e){ if(e.key==='Enter'){ applyInputRange(); e.preventDefault(); } });
  });

  function applyPreset(hours){
    if (hours === null){
      Plotly.relayout(gd, {'xaxis.autorange': true});
      return;
    }
    var r = currentXRange(), end = (r && r[1]) ? new Date(r[1]) : new Date();
    var start = new Date(end.getTime() - hours*3600*1000);
    Plotly.relayout(gd, {'xaxis.range':[start, end]});
  }
  panel.querySelectorAll('.chip').forEach(function(chip){
    chip.addEventListener('click', function(){
      var txt = chip.textContent.trim();
      var map = { '1h':1, '6h':6, '1d':24, '1w':168, '2w':336, '1m':720 };
      applyPreset(map[txt] ?? null);
    });
  });

  // ---------- Toolbar logic ----------
  var btnPan  = panel.querySelector('.toolbtn:nth-of-type(1)');
  var btnZoom = panel.querySelector('.toolbtn:nth-of-type(2)');
  var btnLeft = panel.querySelector('.toolbtn:nth-of-type(3)');
  var btnRight= panel.querySelector('.toolbtn:nth-of-type(4)');
  var btnOut  = panel.querySelector('.toolbtn:nth-of-type(5)');
  var btnIn   = panel.querySelector('.toolbtn:nth-of-type(6)');
  var btnAutoX= panel.querySelector('.toolbtn:nth-of-type(7)');
  var btnAutoY= panel.querySelector('.toolbtn:nth-of-type(8)');
  var btnReset= panel.querySelector('.toolbtn:nth-of-type(9)');
  var btnShot = panel.querySelector('.toolbtn:nth-of-type(10)');
  var btnDN   = panel.querySelector('.toolbtn:nth-of-type(11)');

  function setMode(mode){
    Plotly.relayout(gd, {'dragmode': mode});
    btnPan.setAttribute('aria-pressed', String(mode==='pan'));
    btnZoom.setAttribute('aria-pressed', String(mode==='zoom'));
  }
  btnPan.addEventListener('click', function(){ setMode('pan'); });
  btnZoom.addEventListener('click', function(){ setMode('zoom'); });

  function currentX(){ var r=currentXRange(); return r ? {s:r[0], e:r[1], span:r[1]-r[0]} : null; }
  function panBy(frac){
    var r = currentX(); if(!r) return;
    var shift = r.span*frac;
    Plotly.relayout(gd, {'xaxis.range':[new Date(r.s+shift), new Date(r.e+shift)]});
  }
  btnLeft.addEventListener('click',  function(){ panBy(-0.3); });
  btnRight.addEventListener('click', function(){ panBy(+0.3); });

  function zoomBy(f){
    var r = currentX(); if(!r) return;
    var c = (r.s+r.e)/2, half = r.span/2;
    var newHalf = Math.max(1000, half*f);
    Plotly.relayout(gd, {'xaxis.range':[new Date(c-newHalf), new Date(c+newHalf)]});
  }
  btnIn .addEventListener('click', function(){ zoomBy(0.7); });
  btnOut.addEventListener('click', function(){ zoomBy(1.3); });

  btnAutoX.addEventListener('click', function(){ Plotly.relayout(gd, {'xaxis.autorange': true}); });
  btnAutoY.addEventListener('click', function(){ Plotly.relayout(gd, {'yaxis.autorange': true, 'yaxis2.autorange': true}); });

  btnReset.addEventListener('click', function(){
    Plotly.relayout(gd, {
      'xaxis.autorange': true,
      'yaxis.autorange': true,
      'yaxis2.autorange': true,
      'dragmode': 'pan'
    });
    setMode('pan');
  });

  btnShot.addEventListener('click', function(){
    Plotly.downloadImage(gd, {format:'png', filename:'enmo_screenshot', scale:2})
      .catch(function(e){ console.warn('downloadImage failed', e); });
  });

  // Y-axis preset buttons (ENMO)
  var btnY02 = panel.querySelectorAll('.toolbtn')[12];
  var btnY05 = panel.querySelectorAll('.toolbtn')[13];
  var btnY10 = panel.querySelectorAll('.toolbtn')[14];
  var btnYAu = panel.querySelectorAll('.toolbtn')[15];

  btnY02.addEventListener('click', function(){ Plotly.relayout(gd, {'yaxis.autorange': false, 'yaxis.range':[0,0.2]}); });
  btnY05.addEventListener('click', function(){ Plotly.relayout(gd, {'yaxis.autorange': false, 'yaxis.range':[0,0.5]}); });
  btnY10.addEventListener('click', function(){ Plotly.relayout(gd, {'yaxis.autorange': false, 'yaxis.range':[0,1]}); });
  btnYAu.addEventListener('click', function(){ Plotly.relayout(gd, {'yaxis.autorange': true}); });

  // ---------- Day/Night toggle ----------
  var DAY_RGB   = "__DAY_RGB__";   // '255,247,188'
  var NIGHT_RGB = "__NIGHT_RGB__"; // '40,55,71'
  function setDayNightVisible(show){
    var shapes = (gd.layout.shapes || []).slice();
    var annots = (gd.layout.annotations || []).slice();
    var dirty = false;

    for (var i=0; i<shapes.length; i++){
      var s = shapes[i]; if (!s) continue;
      if (s.fillcolor && (s.fillcolor.indexOf(DAY_RGB) > -1 || s.fillcolor.indexOf(NIGHT_RGB) > -1)){
        if (!!s.visible !== show){ shapes[i].visible = show; dirty = true; }
      }
    }
    for (var j=0; j<annots.length; j++){
      var a = annots[j]; if (!a) continue;
      if (a.text && (a.text.indexOf('☀')>-1 || a.text.indexOf('🌙')>-1)){
        if (!!a.visible !== show){ annots[j].visible = show; dirty = true; }
      }
    }
    if (dirty){ Plotly.relayout(gd, {shapes: shapes, annotations: annots}); }
    btnDN.setAttribute('aria-pressed', String(show));
  }
  btnDN.addEventListener('click', function(){
    var pressed = btnDN.getAttribute('aria-pressed') === 'true';
    setDayNightVisible(!pressed);
  });

  // ---------- Lists: Events and Battery ----------
  function gatherGroupTraces(groupName){
    var map = {};         // name -> [indices...]
    var colorByName = {}; // name -> marker/line color (first trace wins)
    for (var i=0; i<gd.data.length; i++){
      var tr = gd.data[i];
      if (!tr) continue;
      if (tr.legendgroup === groupName){
        var name = tr.name || (groupName+' '+i);
        (map[name] ||= []).push(i);
        var color = (tr.marker && tr.marker.color) || (tr.line && tr.line.color);
        if (color && colorByName[name] == null) colorByName[name] = color;
      }
    }
    return {map, colorByName};
  }
  function isTraceVisible(idx){
    var tr = gd.data[idx];
    return !(tr.visible === 'legendonly' || tr.visible === false);
  }
  function setVisibility(indices, show){
    if (!indices || indices.length===0) return;
    Plotly.restyle(gd, {visible: show ? true : false}, indices);
  }

  function buildList(targetListElem, groupName, allOnBtn, allOffBtn){
    targetListElem.innerHTML = '';
    var info = gatherGroupTraces(groupName);
    var names = Object.keys(info.map).sort();
    names.forEach(function(name){
      var indices = info.map[name];
      var row = document.createElement('label'); row.className='item'; row.title = name;
      var sw = document.createElement('input'); sw.type='checkbox'; sw.className='sw';
      sw.checked = indices.some(isTraceVisible);
      sw.addEventListener('change', function(){ setVisibility(indices, sw.checked); });

      var chip = document.createElement('span'); chip.className='chip-color';
      chip.style.background = info.colorByName[name] || 'currentColor';

      var label = document.createElement('span'); label.className='label'; label.textContent = name;

      row.append(sw, chip, label);
      targetListElem.appendChild(row);
    });

    allOnBtn.onclick  = function(){
      var allIdx = Object.values(info.map).flat(); setVisibility(allIdx, true);
      targetListElem.querySelectorAll('input[type=checkbox]').forEach(cb=>cb.checked=true);
    };
    allOffBtn.onclick = function(){
      var allIdx = Object.values(info.map).flat(); setVisibility(allIdx, false);
      targetListElem.querySelectorAll('input[type=checkbox]').forEach(cb=>cb.checked=false);
    };
  }

  function syncChecks(targetListElem, groupName){
    var info = gatherGroupTraces(groupName);
    targetListElem.querySelectorAll('.item').forEach(function(row){
      var name = row.querySelector('.label').textContent;
      var idxs = info.map[name] || [];
      var vis = idxs.some(isTraceVisible);
      var cb = row.querySelector('input[type=checkbox]');
      if (cb) cb.checked = vis;
    });
  }

  // ---------- Keep PAEMA shapes/annotations in sync with dummy legend item ----------
  var PAEMA_COLOR = "__PAEMA_COLOR__";
  function paemaVisibleFlag(){
    for (var i = 0; i < gd.data.length; i++){
      var tr = gd.data[i];
      if (tr && tr.name === "PAEMA"){
        return !(tr.visible === 'legendonly' || tr.visible === false);
      }
    }
    return true;
  }
  function syncPaemaVisibility(){
    var show = paemaVisibleFlag();
    var shapes = (gd.layout.shapes || []).slice();
    var annots = (gd.layout.annotations || []).slice();
    var dirty = false;

    for (var i=0; i<shapes.length; i++){
      var s = shapes[i]; if (!s) continue;
      var isBand = s.fillcolor && s.fillcolor.indexOf('46,204,113') > -1;
      var isLine = s.line && s.line.color === PAEMA_COLOR;
      if (isBand || isLine){
        if (!!s.visible !== show){ shapes[i].visible = show; dirty = true; }
      }
    }
    for (var j=0; j<annots.length; j++){
      var a = annots[j]; if (!a) continue;
      if (a.arrowcolor === PAEMA_COLOR){
        if (!!a.visible !== show){ annots[j].visible = show; dirty = true; }
      }
    }
    if (dirty){
      Plotly.relayout(gd, {shapes: shapes, annotations: annots});
    }
  }

  // ---------- Hooks ----------
  gd.on('plotly_afterplot', function(){
    syncFromPlot(); positionPanel();
    buildList(panel.querySelectorAll('.list')[0], 'Events',  panel.querySelectorAll('.row .toolbtn')[0], panel.querySelectorAll('.row .toolbtn')[1]);
    buildList(panel.querySelectorAll('.list')[1], 'Battery', panel.querySelectorAll('.row .toolbtn')[2], panel.querySelectorAll('.row .toolbtn')[3]);
    syncPaemaVisibility();
  });
  gd.on('plotly_relayout', function(){ syncFromPlot(); positionPanel(); syncPaemaVisibility(); });
  gd.on('plotly_restyle', function(){
    syncChecks(panel.querySelectorAll('.list')[0], 'Events');
    syncChecks(panel.querySelectorAll('.list')[1], 'Battery');
    syncPaemaVisibility();
  });

  // Initial sync
  syncFromPlot(); positionPanel();
  buildList(panel.querySelectorAll('.list')[0], 'Events',  panel.querySelectorAll('.row .toolbtn')[0], panel.querySelectorAll('.row .toolbtn')[1]);
  buildList(panel.querySelectorAll('.list')[1], 'Battery', panel.querySelectorAll('.row .toolbtn')[2], panel.querySelectorAll('.row .toolbtn')[3]);
  syncPaemaVisibility();
})();
"""




# ===================== DEFAULT VISIBILITY PATCH (paste near the end) =====================
# Goal: show only ENMO points by default; everything else starts hidden (legend-click to show).
def _show_only_enmo_points_by_default(
    fig,
    enmo_names=("ENMO (points)", "ENMO points", "ENMO•points"),
    also_match_contains=("enmo",),
    keep_always=("__threshold__",)   # keep the threshold trace ON
):
    # 1) Hide all traces except the ones we want always visible
    for tr in fig.data:
        name = (tr.name or "")
        tr.visible = True if name in keep_always else "legendonly"

    # 2) Turn ON ENMO point traces
    shown_any = False
    for tr in fig.data:
        name = (tr.name or "")
        name_l = name.lower()
        match_name = (name in enmo_names) or any(sub in name_l for sub in also_match_contains)
        is_points = hasattr(tr, "mode") and tr.mode and ("markers" in tr.mode)
        if match_name and is_points:
            tr.visible = True
            shown_any = True

    # 3) Fallback so plot isn't empty
    if not shown_any:
        for tr in fig.data:
            if hasattr(tr, "mode") and tr.mode and ("markers" in tr.mode):
                tr.visible = True
                break

    # 4) Shapes & annotations:
    #    • Keep Day/Night rectangles visible
    #    • Hide PAEMA window bands and vertical linesw
    if getattr(fig.layout, "shapes", None):
        for s in fig.layout.shapes:
            fc = getattr(s, "fillcolor", "")
            # FIX: s.line is an object; use attribute access, not .get
            lc = getattr(getattr(s, "line", None), "color", None)

            is_day   = isinstance(fc, str) and "255,247,188" in fc   # DAY_FILL_RGBA
            is_night = isinstance(fc, str) and "40,55,71"   in fc    # NIGHT_FILL_RGBA
            is_paema_band = isinstance(fc, str) and "46,204,113" in fc  # PAEMA band fill
            # Accept either hex or rgb() forms for the PAEMA line color
            lc_str = (lc or "").lower()
            is_paema_line = (lc_str == COLORS["PAEMA"].lower()) or ("51,160,44" in lc_str)  # '#33a02c' == rgb(51,160


# --- apply it ---
_show_only_enmo_points_by_default(fig)
# =================== END DEFAULT VISIBILITY PATCH ===================

# (now do your usual)
# fig.show()
# fig.write_html("your_file.html", include_plotlyjs="cdn")



# -- Inject tokens for PAEMA color + day/night RGB and write HTML --
POST_SCRIPT = (
    POST_SCRIPT
    .replace("__PAEMA_COLOR__", COLORS["PAEMA"])
    .replace("__DAY_RGB__", "255,247,188")
    .replace("__NIGHT_RGB__", "40,55,71")
)

fig.write_html(
    str(html_out),
    include_plotlyjs="cdn",
    full_html=True,
    config=PLOT_CONFIG,
    post_script=POST_SCRIPT,   # merged compact panel with pan/zoom/screenshot + Y-scale + battery toggles + day/night
    div_id="enmo_plot"
)

print(f"Saved: {html_out}")

#working
