
import os
from pathlib import Path
import shutil
import numpy as np
import pandas as pd
import streamlit as st
import pydeck as pdk

st.set_page_config(page_title='Route Search Tool', layout='wide')
MASTER_CSV = os.environ.get("MASTER_CSV", "FinalSchedule_normalized.csv")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASS", "Delta01$")
DATA_XLSX = os.environ.get("DATA_XLSX", "map1.xlsx")
IATA_LATLONG_CSV = os.environ.get("IATA_LATLONG_CSV", "iata_latlong.csv")

DISPLAY_COLS = ['Dest', 'Origin', 'Freq', 'A/L', 'EQPT', 'Eff Date', 'Term Date']
FLEET_ALLOWED = ["220","320","737","757/767","764","330","350","717","RJ","Other"]

if st.session_state.get("_trigger_hard_reset_", False):
    try: st.cache_data.clear()
    except Exception: pass
    try: st.cache_resource.clear()
    except Exception: pass
    for k in list(st.session_state.keys()):
        try: del st.session_state[k]
        except Exception: pass
    st.rerun()

st.title('Route Search Tool')

def restart_app(full_reset: bool = True):
    st.session_state["_trigger_hard_reset_"] = True
    st.rerun()

def parse_dates(series: pd.Series) -> pd.Series:
    parsed = pd.to_datetime(series, format='%d%b%y', errors='coerce')
    mask = parsed.isna() & series.notna()
    if mask.any():
        parsed.loc[mask] = pd.to_datetime(series.loc[mask], errors='coerce')
    return parsed

def ensure_display_cols(df: pd.DataFrame) -> pd.DataFrame:
    for c in DISPLAY_COLS:
        if c not in df.columns:
            df[c] = pd.NA
    return df[DISPLAY_COLS].copy()

@st.cache_data(show_spinner=True)
def get_display_df() -> pd.DataFrame:
    if Path(MASTER_CSV).exists():
        df = pd.read_csv(MASTER_CSV, dtype=str)
        if 'Eff Date' in df.columns: df['Eff Date'] = parse_dates(df['Eff Date'])
        if 'Term Date' in df.columns: df['Term Date'] = parse_dates(df['Term Date'])
        df = ensure_display_cols(df)
        return df
    else:
        base = pd.DataFrame(columns=DISPLAY_COLS)
        base.to_csv(MASTER_CSV, index=False)
        return base

def map_to_fleet(eqpt: str) -> str:
    if pd.isna(eqpt): return "Other"
    eqpt = str(eqpt).strip().upper()
    if eqpt in ["75C","75D","76K","75S","75H","75Y","75G","76L","76Z"]:
        return "757/767"
    elif eqpt in ["739","738","73R","73J"]:
        return "737"
    elif eqpt in ["319","320","321","3N1","3NE","32D"]:
        return "320"
    elif eqpt in ["CM7","CM8","CM9","E70","E75","ES4","ES5","RJ6","RJ8","RJ9","RP5"]:
        return "RJ"
    elif eqpt in ["221","223"]:
        return "220"
    elif eqpt == "717":
        return "717"
    elif eqpt == "764":
        return "764"
    elif eqpt.startswith("35"):
        return "350"
    elif eqpt.startswith("33"):
        return "330"
    else:
        return "Other"

# --- Auth (shortened for demo) ---
if "authenticated" not in st.session_state:
    st.session_state["authenticated"] = True  # assume logged in for this demo build

# Load + compute Fleet NOW (so options derive from column, not ad-hoc mapping)
data = get_display_df().copy()
data["EQPT"] = data["EQPT"].astype(str).str.strip()
data["Fleet"] = data["EQPT"].apply(map_to_fleet)

# Sidebar filters
st.sidebar.header("Filters")
if "sel_fleets" not in st.session_state: st.session_state["sel_fleets"] = []
if "sel_eqpts" not in st.session_state: st.session_state["sel_eqpts"] = []

# Build fleet options from whitelist intersecting present values
present_fleets = sorted([f for f in data["Fleet"].dropna().unique().tolist() if f in FLEET_ALLOWED])
fleet_options = present_fleets  # guaranteed whitelist
sel_fleets = st.sidebar.multiselect("Filter Fleet (optional)", fleet_options, default=st.session_state["sel_fleets"])

eqpt_options = sorted(data["EQPT"].dropna().unique().tolist())
sel_eqpts = st.sidebar.multiselect("Filter EQPT (optional)", eqpt_options, default=st.session_state["sel_eqpts"])

st.session_state["sel_fleets"] = sel_fleets
st.session_state["sel_eqpts"] = sel_eqpts

df = data.copy()
if sel_fleets:
    df = df[df["Fleet"].isin(sel_fleets)]
if sel_eqpts:
    df = df[df["EQPT"].isin(sel_eqpts)]

st.subheader("Debug — Current Fleet Options")
st.write(fleet_options)

st.subheader("Sample Output")
st.dataframe(df.head(30))
