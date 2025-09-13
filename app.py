import os
from pathlib import Path
import pandas as pd
import streamlit as st
import pydeck as pdk

# --- Config ---
st.set_page_config(page_title='Route Search Tool', layout='wide')
st.title('Route Search Tool')

# --- Simple Password Gate ---
login_placeholder = st.empty()

if "authenticated" not in st.session_state:
    st.session_state["authenticated"] = False

if not st.session_state["authenticated"]:
    with login_placeholder.container():
        st.subheader("🔑 Enter Password to Continue")
        password = st.text_input("Password", type="password")
        login_btn = st.button("Login")

        if login_btn:
            if password == "FLYDELTA":
                st.session_state["authenticated"] = True
                st.success("✅ Login successful!")
                login_placeholder.empty()
                st.rerun()
            else:
                st.error("❌ Incorrect password.")

if not st.session_state["authenticated"]:
    st.stop()

# --- Main Logout Button ---
if st.sidebar.button("🚪 Logout"):
    st.session_state["authenticated"] = False
    st.rerun()

# --- Admin password (separate from login, used for Upload & Maintenance) ---
ADMIN_PASSWORD = os.environ.get("ADMIN_PASS", "Delta01$")

# Filenames
DATA_XLSX = os.environ.get("DATA_XLSX", "map1.xlsx")
MASTER_CSV = os.environ.get("MASTER_CSV", "FinalSchedule_normalized.csv")

DISPLAY_COLS = ['Dest', 'Origin', 'Freq', 'A/L', 'EQPT', 'Eff Date', 'Term Date']
RENAME_MAP = {
    'STA': 'Dest',
    'PREV CITY': 'Origin',
    'PREV  CITY': 'Origin',
    'PREVCITY': 'Origin',
    'EFF DATE': 'Eff Date',
    'TERM DATE': 'Term Date',
    'FREQ': 'Freq'
}

# ---------- Sidebar Status Box ----------
def show_status_box():
    try:
        if Path(MASTER_CSV).exists():
            df_check = pd.read_csv(MASTER_CSV)
            _rows = len(df_check)
            bad_eff = df_check['Eff Date'].isna().sum() if 'Eff Date' in df_check.columns else 0
            bad_term = df_check['Term Date'].isna().sum() if 'Term Date' in df_check.columns else 0
            msg = f"📊 Master CSV loaded: {_rows} rows"
            if bad_eff > 0 or bad_term > 0:
                msg += f" | ⚠️ Unparsed dates → Eff Date: {bad_eff}, Term Date: {bad_term}"
                st.sidebar.warning(msg)
            else:
                st.sidebar.info(msg)
        else:
            st.sidebar.warning("⚠️ Master CSV not found")
    except Exception as e:
        st.sidebar.error(f"Failed to read master CSV: {e}")

show_status_box()

# ---------- Utilities ----------
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

def clean_origin(df: pd.DataFrame) -> pd.DataFrame:
    if 'Origin' in df.columns:
        df['Origin'] = df['Origin'].astype(str).str.strip()
        df = df[~df['Origin'].isin(['', 'nan', 'NaN', 'None', 'NONE'])]
    return df

@st.cache_data(show_spinner=True)
def get_display_df() -> pd.DataFrame:
    if Path(MASTER_CSV).exists():
        df = pd.read_csv(MASTER_CSV)
        if 'Eff Date' in df.columns:
            df['Eff Date'] = parse_dates(df['Eff Date'])
        if 'Term Date' in df.columns:
            df['Term Date'] = parse_dates(df['Term Date'])
        df = ensure_display_cols(df)
        df = clean_origin(df)
        return df
    else:
        base = pd.DataFrame(columns=DISPLAY_COLS)
        base.to_csv(MASTER_CSV, index=False)
        return base

# ---------- EQPT Group Definitions ----------
EQPT_GROUPS = {
    "757/767": ["75C","75D","76K","75S","75H","75Y","75G","76L","76Z"],
    "737": ["739","738","73R","73J"],
    "320": ["319","320","321","3N1","3NE","32D"],
    "RJ": ["CM7","CM8","CM9","E70","E75","ES4","ES5","RJ6","RJ8","RJ9","RP5"],
    "220": ["221","223"],
    "350": "35",
    "330": "33",
}

def mask_for_fleet(df: pd.DataFrame, selections: list):
    if not selections:
        return pd.Series(True, index=df.index)
    masks = []
    for sel in selections:
        codes = EQPT_GROUPS.get(sel)
        if isinstance(codes, list):
            masks.append(df['EQPT'].astype(str).isin(codes))
        elif isinstance(codes, str):
            masks.append(df['EQPT'].astype(str).str.startswith(codes))
    return pd.concat(masks, axis=1).any(axis=1) if masks else pd.Series(True, index=df.index)

def mask_for_variants(df: pd.DataFrame, selections: list):
    if not selections:
        return pd.Series(True, index=df.index)
    return df['EQPT'].astype(str).isin(selections)

# ---------- Main App ----------
data = get_display_df()
orig_options = sorted([x for x in data['Origin'].dropna().astype(str).unique().tolist() if len(x) > 0])
dest_options = sorted([x for x in data['Dest'].dropna().astype(str).unique().tolist() if len(x) > 0])
eqpt_options = sorted([x for x in data['EQPT'].dropna().astype(str).unique().tolist() if len(x) > 0])

st.sidebar.header('Filters')
sel_date = st.sidebar.date_input('Select Date', value=pd.Timestamp.today().date())
sel_origs = st.sidebar.multiselect('Filter Origin (optional)', orig_options)
sel_dests = st.sidebar.multiselect('Filter Dest (optional)', dest_options)

# Fleet vs Variant filters
sel_fleet = st.sidebar.multiselect('Fleet Filter (grouped)', list(EQPT_GROUPS.keys()))
sel_variants = st.sidebar.multiselect('Variant Filter (raw EQPT)', eqpt_options)

if st.sidebar.button("Reset Filters"):
    sel_origs, sel_dests, sel_fleet, sel_variants = [], [], [], []
    st.rerun()

if st.sidebar.button("🔄 Restart App"):
    st.rerun()

# ---------- Apply Filters ----------
df = data.copy()
sel_ts = pd.Timestamp(sel_date)

if 'Eff Date' in df.columns and 'Term Date' in df.columns:
    df['Eff Date'] = parse_dates(df['Eff Date'])
    df['Term Date'] = parse_dates(df['Term Date'])
    mask_date = (
        (df['Eff Date'].notna()) &
        (df['Term Date'].notna()) &
        (df['Eff Date'] <= sel_ts) &
        (df['Term Date'] >= sel_ts)
    )
    df = df[mask_date]

if sel_dests:
    df = df[df['Dest'].astype(str).isin(sel_dests)]
if sel_origs:
    df = df[df['Origin'].astype(str).isin(sel_origs)]

mask_fleet = mask_for_fleet(df, sel_fleet)
mask_variants = mask_for_variants(df, sel_variants)
df = df[mask_fleet & mask_variants]

# ---------- Show Unique Destinations ----------
unique_dests = sorted(df['Dest'].dropna().astype(str).unique().tolist())
st.subheader("Destinations (unique)")

if unique_dests:
    num_cols = 7
    rows = [unique_dests[i:i+num_cols] for i in range(0, len(unique_dests), num_cols)]
    for row in rows:
        if len(row) < num_cols:
            row.extend([""] * (num_cols - len(row)))
    dest_df = pd.DataFrame(rows)

    st.markdown(
        """
        <style>
        div[data-testid="stDataFrame"] table {
            font-size: 12px !important;
        }
        </style>
        """, unsafe_allow_html=True
    )

    st.dataframe(dest_df, width='stretch', height=200, hide_index=True)
else:
    st.write("No destinations match the current filters.")

# ---------- Show Filtered Results ----------
st.subheader('Filtered Results')
st.write(f'Date: {sel_date} | Rows: {len(df)}')
st.dataframe(df, width='stretch')
st.caption('Showing only columns: Dest, Origin, Freq, A/L, EQPT, Eff Date, Term Date')

# ---------- Map of Unique Destinations (with tooltips) ----------
try:
    ref_path = "map1.xlsx"  # adjust if needed
    ref_df = pd.read_excel(ref_path, sheet_name=0)

    # Normalize column names
    ref_df.columns = [c.strip() for c in ref_df.columns]
    ref_df = ref_df.rename(columns={"IATA Code": "Dest", "LAT": "lat", "LONG": "lon"})

    # Merge unique destinations with reference lat/lon
    dest_locations = ref_df[ref_df["Dest"].isin(unique_dests)][["Dest", "lat", "lon"]].dropna()

    st.subheader("Destination Map")
    if not dest_locations.empty:
        layer = pdk.Layer(
            "ScatterplotLayer",
            data=dest_locations,
            get_position='[lon, lat]',
            get_radius=40000,
            get_fill_color=[0, 128, 255, 180],
            pickable=True,
        )

        view_state = pdk.ViewState(
            latitude=float(dest_locations["lat"].mean()),
            longitude=float(dest_locations["lon"].mean()),
            zoom=3,
            pitch=0,
        )

        r = pdk.Deck(
            layers=[layer],
            initial_view_state=view_state,
            tooltip={"text": "{Dest}"}
        )

        st.pydeck_chart(r)
    else:
        st.info("⚠️ No matching coordinates found for current destinations.")
except Exception as e:
    st.error(f"Map could not be generated: {e}")
