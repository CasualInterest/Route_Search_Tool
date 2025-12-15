import os
from pathlib import Path
import shutil
import numpy as np
import pandas as pd
import streamlit as st
import pydeck as pdk
from datetime import datetime
from supabase import create_client, Client

# =========================
# Config
# =========================
st.set_page_config(page_title="Route Search Tool", layout="wide")

# Supabase connection
def get_supabase_client() -> Client:
    """Get Supabase client from secrets"""
    try:
        url = st.secrets["supabase"]["url"]
        key = st.secrets["supabase"]["key"]
        return create_client(url, key)
    except Exception as e:
        st.error(f"Supabase configuration error: {e}")
        st.info("Add 'supabase' section to Streamlit Secrets with 'url' and 'key'")
        st.stop()

supabase = get_supabase_client()
TABLE_NAME = "routes"  # Your Supabase table name

IATA_LATLONG_CSV = os.environ.get("IATA_LATLONG_CSV", "iata_latlong.csv")
BACKUP_DIR = Path("backups")

DISPLAY_COLS = ["Dest", "Origin", "Freq", "A/L", "EQPT", "Eff Date", "Term Date"]
FLEET_ALLOWED = ["220", "320", "737", "757/767", "764", "330", "350", "717", "RJ", "Other"]

# Never store flight/dep details
SENSITIVE_COLS = [
    "Flight", "Flight #", "Flt#", "Flt", "FLT", "FLIGHT",
    "Dep Time", "Departure Time", "STD", "ETD", "Dept Time", "DEP TIME",
]

# =========================
# Secrets / Passwords
# =========================
def _get_secret(key: str):
    try:
        return st.secrets[key]
    except Exception:
        return os.environ.get(key)

VIEW_PASSWORD = _get_secret("VIEW_PASSWORD")
ADMIN_PASSWORD = _get_secret("ADMIN_PASSWORD")

# =========================
# Viewer Auth Gate
# =========================
if "viewer_authenticated" not in st.session_state:
    st.session_state["viewer_authenticated"] = False

if not st.session_state["viewer_authenticated"]:
    st.title("Route Search Tool")
    st.subheader("🔒 Enter Password to Continue")

    if not VIEW_PASSWORD:
        st.error("Viewer password not configured. Add VIEW_PASSWORD in Streamlit Secrets.")
        st.stop()

    pw = st.text_input("Password", type="password")
    if st.button("Login", type="primary"):
        if pw == VIEW_PASSWORD:
            st.session_state["viewer_authenticated"] = True
            st.rerun()
        else:
            st.error("Incorrect password.")
    st.stop()

# =========================
# Hard reset helper
# =========================
def hard_reset():
    """Full nuke reset, only used by the manual Restart App button."""
    try:
        st.cache_data.clear()
    except Exception:
        pass
    try:
        st.cache_resource.clear()
    except Exception:
        pass
    for _k in list(st.session_state.keys()):
        try:
            del st.session_state[_k]
        except Exception:
            pass
    st.rerun()

st.title("Route Search Tool")

# Viewer logout
with st.sidebar:
    if st.button("🚪 Logout (Viewer)", use_container_width=True):
        st.session_state["viewer_authenticated"] = False
        st.session_state["is_admin"] = False
        st.rerun()

# =========================
# Helpers
# =========================
def _to_na(s: pd.Series) -> pd.Series:
    return (
        s.astype(str)
        .str.strip()
        .replace({"": np.nan, "nan": np.nan, "NaN": np.nan, "None": np.nan})
    )

def parse_any_date(series: pd.Series) -> pd.Series:
    s = _to_na(series)
    dt = pd.to_datetime(s, format="%d%b%y", errors="coerce")
    m = dt.isna()
    if m.any():
        dt.loc[m] = pd.to_datetime(
            s.loc[m],
            errors="coerce",
            dayfirst=False,
            infer_datetime_format=True,
        )
    m = dt.isna()
    if m.any():
        as_num = pd.to_numeric(s.loc[m], errors="coerce")
        num_mask = as_num.notna()
        if num_mask.any():
            dt.loc[as_num.index[num_mask]] = pd.to_datetime(
                as_num[num_mask],
                unit="d",
                origin="1899-12-30",
                errors="coerce",
            )
    return dt

def ensure_display_cols(df: pd.DataFrame) -> pd.DataFrame:
    df = df.drop(columns=[c for c in df.columns if c in SENSITIVE_COLS], errors="ignore")
    for c in DISPLAY_COLS:
        if c not in df.columns:
            df[c] = pd.NA
    return df[DISPLAY_COLS].copy()

def clean_origin(df: pd.DataFrame) -> pd.DataFrame:
    if "Origin" in df.columns:
        df["Origin"] = df["Origin"].astype(str).str.strip()
        df = df[~df["Origin"].isin(["", "nan", "NaN", "None", "NONE"])]
    return df

# =========================
# Supabase Data Functions
# =========================

@st.cache_data(ttl=60, show_spinner="Loading data from database...")
def load_all_data_from_supabase() -> pd.DataFrame:
    """Load all routes from Supabase"""
    try:
        # Fetch all data (Supabase has built-in pagination handling)
        response = supabase.table(TABLE_NAME).select("*").execute()
        
        if not response.data:
            return pd.DataFrame(columns=DISPLAY_COLS)
        
        df = pd.DataFrame(response.data)
        
        # Parse dates
        if "Eff Date" in df.columns:
            df["Eff Date"] = parse_any_date(df["Eff Date"])
        if "Term Date" in df.columns:
            df["Term Date"] = parse_any_date(df["Term Date"])
        
        df = ensure_display_cols(df)
        df = clean_origin(df)
        
        return df
    
    except Exception as e:
        st.error(f"Failed to load data from Supabase: {e}")
        return pd.DataFrame(columns=DISPLAY_COLS)

def upload_to_supabase(df: pd.DataFrame, batch_size: int = 1000) -> bool:
    """Upload DataFrame to Supabase in batches"""
    try:
        # Ensure correct columns
        df = ensure_display_cols(df)
        
        # Convert dates to strings for JSON serialization
        if "Eff Date" in df.columns:
            df["Eff Date"] = pd.to_datetime(df["Eff Date"], errors="coerce").dt.strftime("%Y-%m-%d")
        if "Term Date" in df.columns:
            df["Term Date"] = pd.to_datetime(df["Term Date"], errors="coerce").dt.strftime("%Y-%m-%d")
        
        # Replace NaN with None
        df = df.where(pd.notna(df), None)
        
        # Upload in batches
        total = len(df)
        for i in range(0, total, batch_size):
            batch = df.iloc[i:i+batch_size]
            records = batch.to_dict('records')
            supabase.table(TABLE_NAME).insert(records).execute()
        
        return True
    
    except Exception as e:
        st.error(f"Upload to Supabase failed: {e}")
        return False

def merge_and_upsert_to_supabase(incoming_df: pd.DataFrame) -> tuple[int, int]:
    """
    Merge incoming data with existing data and upsert to Supabase.
    Returns (rows_before, rows_after)
    """
    try:
        # Load existing data
        existing = load_all_data_from_supabase()
        rows_before = len(existing)
        
        # Combine and deduplicate
        combined = pd.concat([existing, incoming_df], ignore_index=True)
        combined = combined.drop_duplicates(subset=DISPLAY_COLS, keep="last")
        combined = combined.sort_values(DISPLAY_COLS, kind="mergesort", ignore_index=True)
        
        rows_after = len(combined)
        
        # Clear table and upload new data
        # Delete all existing rows
        supabase.table(TABLE_NAME).delete().neq("Dest", "").execute()
        
        # Upload merged data
        success = upload_to_supabase(combined)
        
        if success:
            # Clear cache so new data is loaded
            st.cache_data.clear()
            return rows_before, rows_after
        else:
            return rows_before, rows_before
    
    except Exception as e:
        st.error(f"Merge failed: {e}")
        return 0, 0

def backup_to_csv() -> Path:
    """Download current Supabase data to local CSV backup"""
    try:
        BACKUP_DIR.mkdir(exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = BACKUP_DIR / f"backup_{timestamp}.csv"
        
        df = load_all_data_from_supabase()
        df.to_csv(backup_path, index=False)
        
        return backup_path
    except Exception as e:
        st.error(f"Backup failed: {e}")
        return None

def clear_all_data():
    """Delete all rows from Supabase table"""
    try:
        supabase.table(TABLE_NAME).delete().neq("Dest", "").execute()
        st.cache_data.clear()
        return True
    except Exception as e:
        st.error(f"Clear failed: {e}")
        return False

# =========================
# Upload file parsing
# =========================

def read_map_upload(file_like) -> pd.DataFrame:
    name = getattr(file_like, "name", "").lower()

    if name.endswith(".xlsx") or name.endswith(".xls"):
        df = pd.read_excel(file_like, header=0, skiprows=4, dtype=str, engine="openpyxl")
    else:
        df = pd.read_csv(
            file_like,
            header=0,
            skiprows=4,
            dtype=str,
            encoding="utf-8",
            on_bad_lines="skip",
        )

    df.columns = [str(c).strip().title() for c in df.columns]
    df = df.drop(columns=[c for c in df.columns if c in SENSITIVE_COLS], errors="ignore")

    rename_map = {
        "Sta": "Dest",
        "Dest (Sta)": "Dest",
        "Destination": "Dest",
        "Prev City": "Origin",
        "Prev  City": "Origin",
        "Prevcity": "Origin",
        "Eff Date": "Eff Date",
        "Term Date": "Term Date",
        "Freq": "Freq",
        "A/L": "A/L",
        "Eqpt": "EQPT",
    }
    df = df.rename(columns={c: rename_map.get(c, c) for c in df.columns})

    if "Eff Date" in df.columns:
        df["Eff Date"] = parse_any_date(df["Eff Date"])
    if "Term Date" in df.columns:
        df["Term Date"] = parse_any_date(df["Term Date"])

    df = ensure_display_cols(df)
    df = clean_origin(df)
    
    return df

def clean_master_df(df: pd.DataFrame):
    """Clean and validate data, return (cleaned_df, total_dropped, dropped_dates, dropped_origin)"""
    original_len = len(df)
    
    # Ensure display cols
    df = ensure_display_cols(df)
    
    # Parse dates
    if "Eff Date" in df.columns:
        df["Eff Date"] = parse_any_date(df["Eff Date"])
    if "Term Date" in df.columns:
        df["Term Date"] = parse_any_date(df["Term Date"])
    
    # Drop rows with missing dates
    before_date_drop = len(df)
    df = df.dropna(subset=["Eff Date", "Term Date"])
    dropped_dates = before_date_drop - len(df)
    
    # Drop rows with blank Origin
    before_origin_drop = len(df)
    df = clean_origin(df)
    dropped_origin = before_origin_drop - len(df)
    
    total_dropped = original_len - len(df)
    
    return df, total_dropped, dropped_dates, dropped_origin

def map_to_fleet(eqpt: str) -> str:
    """Map EQPT code to fleet category"""
    if pd.isna(eqpt):
        return "Other"
    s = str(eqpt).strip().upper()
    
    if "220" in s or "A220" in s:
        return "220"
    if "32" in s or "A32" in s or "320" in s or "321" in s:
        return "320"
    if "737" in s or "73" in s:
        return "737"
    if "757" in s or "767" in s or "75" in s or "76" in s:
        return "757/767"
    if "764" in s:
        return "764"
    if "330" in s or "A330" in s or "33" in s:
        return "330"
    if "350" in s or "A350" in s or "35" in s:
        return "350"
    if "717" in s:
        return "717"
    if "RJ" in s or "CRJ" in s or "ERJ" in s or "E17" in s or "E19" in s:
        return "RJ"
    return "Other"

# =========================
# Load Data
# =========================
data = load_all_data_from_supabase()

if data.empty:
    st.warning("⚠️ No data in database. Upload MAP files to get started.")

# =========================
# Sidebar - Filters
# =========================
with st.sidebar:
    st.header("Filters")
    
    sel_date = st.date_input("Select Date", value=datetime.today())
    
    all_dests = sorted(data["Dest"].dropna().astype(str).unique()) if not data.empty else []
    all_origs = sorted(data["Origin"].dropna().astype(str).unique()) if not data.empty else []
    all_eqpts = sorted(data["EQPT"].dropna().astype(str).unique()) if not data.empty else []
    
    sel_dests = st.multiselect("Filter Dest (optional)", options=all_dests)
    sel_origs = st.multiselect("Filter Origin (optional)", options=all_origs)
    sel_fleets = st.multiselect("Filter Fleet (optional)", options=FLEET_ALLOWED)
    sel_eqpts = st.multiselect("Filter EQPT (optional)", options=all_eqpts)
    
    if st.button("Reset Filters", use_container_width=True):
        st.rerun()

# =========================
# Admin Section
# =========================
if "is_admin" not in st.session_state:
    st.session_state["is_admin"] = False

with st.sidebar:
    st.markdown("---")
    st.subheader("Admin")
    
    if not st.session_state["is_admin"]:
        admin_pw = st.text_input("Admin Password", type="password", key="admin_login")
        if st.button("Login Admin", use_container_width=True):
            if admin_pw == ADMIN_PASSWORD:
                st.session_state["is_admin"] = True
                st.rerun()
            else:
                st.error("Incorrect admin password.")
    else:
        st.success("✅ Admin mode enabled")
        if st.button("Logout Admin", use_container_width=True):
            st.session_state["is_admin"] = False
            st.rerun()

# =========================
# Admin Upload & Merge
# =========================
if st.session_state.get("is_admin", False):
    st.sidebar.markdown("---")
    
    # Show database info
    total_rows = len(data)
    st.sidebar.info(f"💾 Database: {total_rows:,} rows")
    
    with st.sidebar.expander("➕ Upload & Merge MAP files", expanded=False):
        uploads = st.file_uploader(
            "Upload MAP files (.xlsx or .csv)",
            type=["xlsx", "csv", "xls"],
            accept_multiple_files=True,
            key="map_uploads",
        )
        
        if uploads:
            st.caption("Files to merge:")
            for u in uploads:
                st.write("•", u.name)
        
        if st.button("Process & Merge", type="primary", disabled=not uploads):
            parts, errors = [], []
            
            for up in uploads:
                try:
                    df = read_map_upload(up)
                    parts.append(df)
                except Exception as e:
                    errors.append(f"{getattr(up,'name','file')}: {e}")
            
            if errors:
                st.sidebar.error("Some files failed:\n" + "\n".join(errors))
            
            if parts:
                incoming = pd.concat(parts, ignore_index=True)
                
                # Clean the incoming data
                cleaned, dropped_total, dropped_dates, dropped_origin = clean_master_df(incoming)
                
                if dropped_total > 0:
                    st.sidebar.warning(
                        f"Cleanup dropped {dropped_total} rows "
                        f"(invalid dates: {dropped_dates}, blank origin: {dropped_origin})."
                    )
                
                # Merge and upload to Supabase
                with st.spinner("Uploading to database..."):
                    rows_before, rows_after = merge_and_upsert_to_supabase(cleaned)
                    delta = rows_after - rows_before
                    
                    st.sidebar.success(
                        f"✅ Merge complete!\n"
                        f"Rows: {rows_before:,} → {rows_after:,} (Δ {delta:+,})"
                    )
                    st.rerun()
            else:
                st.sidebar.warning("No valid rows found to merge.")
    
    st.sidebar.markdown("---")
    st.sidebar.subheader("Maintenance")
    
    if st.sidebar.button("💾 Download CSV Backup", use_container_width=True):
        backup_path = backup_to_csv()
        if backup_path:
            with open(backup_path, "rb") as f:
                st.sidebar.download_button(
                    "⬇️ Download Backup",
                    data=f,
                    file_name=f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                    mime="text/csv",
                    use_container_width=True
                )
    
    if st.sidebar.button("🧹 Clean & normalize database", use_container_width=True):
        with st.spinner("Cleaning database..."):
            # Load, clean, and re-upload
            raw_data = load_all_data_from_supabase()
            cleaned, dropped_total, dropped_dates, dropped_origin = clean_master_df(raw_data)
            
            # Clear and re-upload
            clear_all_data()
            upload_to_supabase(cleaned)
            
            st.sidebar.success(
                f"Cleaned database. Dropped {dropped_total} rows "
                f"(invalid dates: {dropped_dates}, blank origin: {dropped_origin}). "
                f"Now {len(cleaned):,} rows."
            )
            st.cache_data.clear()
            st.rerun()
    
    _confirm_clear = st.sidebar.checkbox("Confirm delete all data")
    _btn_clear_all = st.sidebar.button("⚠️ Clear All Data")
    if _btn_clear_all and _confirm_clear:
        backup_to_csv()  # Auto-backup before clearing
        if clear_all_data():
            st.sidebar.success("All data cleared.")
            st.rerun()

# =========================
# Filtering logic
# =========================
df = data.copy()
sel_ts = pd.Timestamp(sel_date)

df["Fleet"] = df["EQPT"].apply(map_to_fleet)

if "Eff Date" in df.columns and "Term Date" in df.columns:
    df["Eff Date"] = parse_any_date(df["Eff Date"])
    df["Term Date"] = parse_any_date(df["Term Date"])
    mask_date = (
        (df["Eff Date"].notna())
        & (df["Term Date"].notna())
        & (df["Eff Date"] <= sel_ts)
        & (df["Term Date"] >= sel_ts)
    )
    df = df[mask_date]

    df["Eff Date"] = df["Eff Date"].dt.strftime("%Y-%m-%d")
    df["Term Date"] = df["Term Date"].dt.strftime("%Y-%m-%d")

if len(sel_dests) > 0:
    df = df[df["Dest"].astype(str).isin(sel_dests)]
if len(sel_origs) > 0:
    df = df[df["Origin"].astype(str).isin(sel_origs)]
if len(sel_fleets) > 0:
    df = df[df["Fleet"].isin(sel_fleets)]
if len(sel_eqpts) > 0:
    df = df[df["EQPT"].astype(str).isin(sel_eqpts)]

# =========================
# Unique Destinations grid
# =========================
def render_unique_dest_table(filtered_df: pd.DataFrame, n_cols: int = 7, height: int = 220):
    st.subheader("Unique Destinations")
    if filtered_df.empty:
        st.info("No data for current filters.")
        return []

    dest_col_candidates = [
        c for c in filtered_df.columns
        if c.strip().lower() in ("dest", "dest (sta)", "sta", "destination")
    ]
    dest_col = dest_col_candidates[0] if dest_col_candidates else "Dest"

    uniq = (
        filtered_df[dest_col]
        .astype(str)
        .str.strip()
        .replace({"nan": np.nan, "None": np.nan, "": np.nan})
        .dropna()
        .unique()
    )
    uniq = np.sort(uniq)
    if len(uniq) == 0:
        st.info("No unique destinations found.")
        return []

    rows = int(np.ceil(len(uniq) / n_cols))
    table = np.empty((rows, n_cols), dtype=object)
    table[:] = ""
    for i, val in enumerate(uniq):
        r = i // n_cols
        c = i % n_cols
        table[r, c] = val

    wide_df = pd.DataFrame(table, columns=[f"Dest {i+1}" for i in range(n_cols)])
    st.dataframe(wide_df, use_container_width=True, height=height)

    return list(uniq)

unique_list = render_unique_dest_table(df, n_cols=7, height=220)

# =========================
# Results Table
# =========================
st.subheader("Filtered Results")
st.write(f"Date: {sel_date} | Rows: {len(df):,}")

show_cols = [
    "Dest", "Origin",
    "A/L", "EQPT", "Fleet", "Eff Date", "Term Date"
]

for c in show_cols:
    if c not in df.columns:
        df[c] = pd.NA

st.dataframe(df[show_cols], use_container_width=True, height=420)
st.caption("Showing: Dest, Origin, A/L, EQPT, Fleet, Eff Date, Term Date")

# =========================
# Map of Unique Destinations
# =========================
@st.cache_data
def load_airports(path: str):
    if not Path(path).exists():
        return pd.DataFrame(columns=["Dest", "Lat", "Long"])
    a = pd.read_csv(path, dtype={"Dest": str, "Lat": float, "Long": float})
    a["Dest"] = a["Dest"].str.strip()
    return a

st.subheader("Map of Unique Destinations")

def render_map(unique_dests: list):
    if not unique_dests:
        st.info("No destinations to plot.")
        return

    airports_df = load_airports(IATA_LATLONG_CSV)

    points = (
        pd.DataFrame({"Dest": unique_dests})
        .merge(airports_df, on="Dest", how="left")
        .dropna(subset=["Lat", "Long"])
    )

    if points.empty:
        st.info("No coordinates available for current selection.")
        return

    deck = pdk.Deck(
        layers=[
            pdk.Layer(
                "ScatterplotLayer",
                data=points,
                get_position="[Long, Lat]",
                get_radius=80000,
                get_fill_color=[0, 120, 220, 160],
                pickable=True,
            )
        ],
        initial_view_state=pdk.ViewState(
            latitude=float(points["Lat"].mean()) if not points["Lat"].isna().all() else 39.0,
            longitude=float(points["Long"].mean()) if not points["Long"].isna().all() else -98.0,
            zoom=3,
            pitch=0,
            bearing=0,
        ),
        tooltip={"text": "Destination: {Dest}"},
        map_provider="mapbox",
        map_style=None,
    )
    st.pydeck_chart(deck)

render_map(unique_list)
