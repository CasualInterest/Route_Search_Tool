import os
from pathlib import Path
import shutil
import numpy as np
import pandas as pd
import streamlit as st
import pydeck as pdk
from datetime import datetime

# =========================
# Config
# =========================
st.set_page_config(page_title='Route Search Tool', layout='wide')

MASTER_CSV = os.environ.get("MASTER_CSV", "FinalSchedule_normalized.csv")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASS", "Delta01$")
DATA_XLSX = os.environ.get("DATA_XLSX", "map1.xlsx")  # only used if master missing
IATA_LATLONG_CSV = os.environ.get("IATA_LATLONG_CSV", "iata_latlong.csv")
BACKUP_DIR = Path("backups")
ROLLING_BACKUP = BACKUP_DIR / "FinalSchedule_backup.csv"

DISPLAY_COLS = ['Dest', 'Origin', 'Freq', 'A/L', 'EQPT', 'Eff Date', 'Term Date']
FLEET_ALLOWED = ['220','320','737','757/767','764','330','350','717','RJ','Other']

# Columns we never want to keep in master (flight/dep details)
SENSITIVE_COLS = [
    'Flight','Flight #','Flt#','Flt','FLT','FLIGHT',
    'Dep Time','Departure Time','STD','ETD','Dept Time','DEP TIME'
]

# =========================
# Safe hard reset helper
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
    # wipe this user's session_state, not global process state
    for _k in list(st.session_state.keys()):
        try:
            del st.session_state[_k]
        except Exception:
            pass
    st.rerun()

st.title('Route Search Tool')

# =========================
# Helpers
# =========================
def _to_na(s: pd.Series) -> pd.Series:
    return (
        s.astype(str)
         .str.strip()
         .replace({'': np.nan, 'nan': np.nan, 'NaN': np.nan, 'None': np.nan})
    )

def parse_any_date(series: pd.Series) -> pd.Series:
    # Try ddMMMyy like 05Oct25, then general parse, then Excel serials
    s = _to_na(series)
    dt = pd.to_datetime(s, format='%d%b%y', errors='coerce')
    m = dt.isna()
    if m.any():
        dt.loc[m] = pd.to_datetime(
            s.loc[m],
            errors='coerce',
            dayfirst=False,
            infer_datetime_format=True
        )
    m = dt.isna()
    if m.any():
        as_num = pd.to_numeric(s.loc[m], errors='coerce')
        num_mask = as_num.notna()
        if num_mask.any():
            dt.loc[as_num.index[num_mask]] = pd.to_datetime(
                as_num[num_mask],
                unit='d',
                origin='1899-12-30',
                errors='coerce'
            )
    return dt

def ensure_display_cols(df: pd.DataFrame) -> pd.DataFrame:
    # Always drop sensitive columns before trimming to DISPLAY_COLS
    df = df.drop(columns=[c for c in df.columns if c in SENSITIVE_COLS], errors='ignore')
    for c in DISPLAY_COLS:
        if c not in df.columns:
            df[c] = pd.NA
    return df[DISPLAY_COLS].copy()

def clean_origin(df: pd.DataFrame) -> pd.DataFrame:
    if 'Origin' in df.columns:
        df['Origin'] = df['Origin'].astype(str).str.strip()
        df = df[~df['Origin'].isin(['', 'nan', 'NaN', 'None', 'NONE'])]
    return df

def load_raw_excel(path: str) -> pd.DataFrame:
    xl = pd.ExcelFile(path)
    sheet = xl.sheet_names[0]

    # detect header row (where STA / PREV CITY appear)
    raw = pd.read_excel(path, sheet_name=sheet, header=None)
    header_idx = None
    for i in range(min(25, len(raw))):
        row_vals = raw.iloc[i].astype(str).str.strip().str.upper().tolist()
        if ('STA' in row_vals) and (
            ('PREV CITY' in row_vals) or
            ('PREV  CITY' in row_vals) or
            ('PREVCITY' in row_vals)
        ):
            header_idx = i
            break
    if header_idx is None:
        header_idx = 0

    df = pd.read_excel(path, sheet_name=sheet, header=header_idx)
    df.columns = [str(c).strip() for c in df.columns]

    # Drop any flight/dep columns from the seed excel as well
    df = df.drop(columns=[c for c in df.columns if c in SENSITIVE_COLS], errors='ignore')
    return df

def _fmt_ts(ts: float) -> str:
    try:
        return datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M')
    except Exception:
        return ''

def _last_updated_text(filepath: str) -> str:
    try:
        p = Path(filepath)
        if p.exists():
            mtime = p.stat().st_mtime
            return _fmt_ts(mtime)
    except Exception:
        pass
    return '—'

@st.cache_data(show_spinner=True)
def _load_master_df_from_disk() -> pd.DataFrame:
    """
    Read master from CSV on disk (MASTER_CSV).
    If missing, attempt fallback excel (DATA_XLSX).
    Return cleaned DataFrame with correct cols, parsed dates, etc.
    """
    # Case 1: master CSV exists
    if Path(MASTER_CSV).exists():
        df = pd.read_csv(MASTER_CSV, dtype=str)
        # Ensure any legacy flight/dep columns are purged
        df = df.drop(columns=[c for c in df.columns if c in SENSITIVE_COLS], errors='ignore')

        if 'Eff Date' in df.columns:
            df['Eff Date'] = parse_any_date(df['Eff Date'])
        if 'Term Date' in df.columns:
            df['Term Date'] = parse_any_date(df['Term Date'])
        df = ensure_display_cols(df)
        df = clean_origin(df)
        return df

    # Case 2: seed Excel
    if Path(DATA_XLSX).exists():
        df = load_raw_excel(DATA_XLSX)
        rename_map = {
            'STA': 'Dest',
            'PREV CITY': 'Origin',
            'PREV  CITY': 'Origin',
            'PREVCITY': 'Origin',
            'EFF DATE': 'Eff Date',
            'TERM DATE': 'Term Date',
            'FREQ': 'Freq',
            'A/L': 'A/L',
            'EQPT': 'EQPT',
        }
        df = df.rename(columns={c: rename_map.get(c, c) for c in df.columns})
        if 'Eff Date' in df.columns:
            df['Eff Date'] = parse_any_date(df['Eff Date'])
        if 'Term Date' in df.columns:
            df['Term Date'] = parse_any_date(df['Term Date'])
        df = ensure_display_cols(df)
        df = clean_origin(df)
        return df

    # Case 3: nothing exists — create empty skeleton
    base = pd.DataFrame(columns=DISPLAY_COLS)
    base.to_csv(MASTER_CSV, index=False)
    return base

def read_map_upload(file_like) -> pd.DataFrame:
    name = getattr(file_like, 'name', '').lower()

    # raw import (skip first 4 rows of junk header)
    if name.endswith('.xlsx') or name.endswith('.xls'):
        df = pd.read_excel(file_like, header=0, skiprows=4, dtype=str, engine='openpyxl')
    else:
        df = pd.read_csv(
            file_like,
            header=0,
            skiprows=4,
            dtype=str,
            encoding='utf-8',
            on_bad_lines='skip'
        )

    # normalize column names
    mapping = {
        'STA': 'Dest',
        'Dest': 'Dest',
        'PREV CITY': 'Origin',
        'PREV  CITY': 'Origin',
        'PREVCITY': 'Origin',
        'Origin': 'Origin',
        'FREQ': 'Freq',
        'Freq': 'Freq',
        'A/L': 'A/L',
        'EQPT': 'EQPT',
        'EFF DATE': 'Eff Date',
        'Eff Date': 'Eff Date',
        'TERM DATE': 'Term Date',
        'Term Date': 'Term Date',
    }
    df.columns = [str(c).strip() for c in df.columns]
    df = df.rename(columns={c: mapping.get(c, c) for c in df.columns})

    # Drop any flight / dep columns from uploaded MAPs
    df = df.drop(columns=[c for c in df.columns if c in SENSITIVE_COLS], errors='ignore')

    df = ensure_display_cols(df)

    # strip spaces
    for c in ['Dest','Origin','Freq','A/L','EQPT']:
        df[c] = df[c].astype(str).str.strip()

    # parse & validate dates / origin
    eff = parse_any_date(df['Eff Date'])
    term = parse_any_date(df['Term Date'])
    origin_ok = df['Origin'].astype(str).str.strip().ne('')
    dates_ok = eff.notna() & term.notna()
    keep_mask = dates_ok & origin_ok

    dropped_dates = int((~dates_ok).sum())
    dropped_origin = int((~origin_ok).sum())
    dropped_total = int((~keep_mask).sum())
    if dropped_total > 0:
        st.sidebar.warning(
            f"Dropped {dropped_total} rows from {getattr(file_like,'name','file')} "
            f"(invalid/missing dates: {dropped_dates}, blank origin: {dropped_origin})."
        )

    df = df.loc[keep_mask].copy()
    df['Eff Date'] = eff.loc[keep_mask].dt.strftime('%Y-%m-%d')
    df['Term Date'] = term.loc[keep_mask].dt.strftime('%Y-%m-%d')
    return df[DISPLAY_COLS].copy()

def backup_master():
    try:
        BACKUP_DIR.mkdir(exist_ok=True)
        if Path(MASTER_CSV).exists():
            shutil.copy(MASTER_CSV, ROLLING_BACKUP)
            return ROLLING_BACKUP
    except Exception as e:
        st.sidebar.warning(f'Backup skipped: {e}')
    return None

def restore_latest_backup():
    try:
        if not ROLLING_BACKUP.exists():
            st.sidebar.error('No rolling backup found.')
            return
        shutil.copy(ROLLING_BACKUP, MASTER_CSV)
        st.sidebar.success(f'Restored rolling backup → {MASTER_CSV}')
        # refresh the cache for everyone
        try:
            st.cache_data.clear()
        except Exception:
            pass
    except Exception as e:
        st.sidebar.error('Restore failed: ' + str(e))

def merge_override(master_df: pd.DataFrame, new_df: pd.DataFrame) -> pd.DataFrame:
    combined = pd.concat([master_df, new_df], ignore_index=True)
    combined = ensure_display_cols(combined)
    # drop full-row dupes based on all DISPLAY_COLS
    combined = combined.drop_duplicates(subset=DISPLAY_COLS, keep='last')
    combined = combined.sort_values(by=DISPLAY_COLS, kind='mergesort', ignore_index=True)
    return combined

def map_to_fleet(eqpt: str) -> str:
    if pd.isna(eqpt):
        return 'Other'
    eqpt = str(eqpt).strip().upper()
    if eqpt in ['75C','75D','76K','75S','75H','75Y','75G','76L','76Z']:
        return '757/767'
    elif eqpt in ['739','738','73R','73J']:
        return '737'
    elif eqpt in ['319','320','321','3N1','3NE','32D']:
        return '320'
    elif eqpt in ['CM7','CM8','CM9','E70','E75','ES4','ES5','RJ6','RJ8','RJ9','RP5']:
        return 'RJ'
    elif eqpt in ['221','223']:
        return '220'
    elif eqpt == '717':
        return '717'
    elif eqpt == '764':
        return '764'
    elif eqpt.startswith('35'):
        return '350'
    elif eqpt.startswith('33'):
        return '330'
    else:
        return 'Other'

def clean_master_df(df: pd.DataFrame):
    # Ensure no sensitive columns remain in master
    df = df.drop(columns=[c for c in df.columns if c in SENSITIVE_COLS], errors='ignore')
    df = ensure_display_cols(df)

    # basic trim
    for c in ['Dest','Origin','Freq','A/L','EQPT']:
        df[c] = df[c].astype(str).str.strip()

    eff = parse_any_date(df['Eff Date'])
    term = parse_any_date(df['Term Date'])
    origin_ok = df['Origin'].astype(str).str.strip().ne('')
    dates_ok = eff.notna() & term.notna()
    keep = dates_ok & origin_ok

    cleaned = df.loc[keep].copy()
    cleaned['Eff Date'] = eff.loc[keep].dt.strftime('%Y-%m-%d')
    cleaned['Term Date'] = term.loc[keep].dt.strftime('%Y-%m-%d')

    cleaned = cleaned.drop_duplicates(subset=DISPLAY_COLS, keep='last')
    cleaned = cleaned.sort_values(by=DISPLAY_COLS, kind='mergesort', ignore_index=True)

    dropped_dates = int((~dates_ok).sum())
    dropped_origin = int((~origin_ok).sum())
    dropped_total = int((~keep).sum())
    return cleaned, dropped_total, dropped_dates, dropped_origin

# =========================
# Auth gate
# =========================
login_placeholder = st.empty()
if 'authenticated' not in st.session_state:
    st.session_state['authenticated'] = False

if not st.session_state['authenticated']:
    with login_placeholder.container():
        st.subheader('🔑 Enter Password to Continue')
        password = st.text_input('Password', type='password')
        login_btn = st.button('Login')
        if login_btn:
            if password == 'FLYDELTA':
                st.session_state['authenticated'] = True
                st.success('✅ Login successful!')
                login_placeholder.empty()
                st.rerun()
            else:
                st.error('❌ Incorrect password.')

# If still not authenticated, stop here (important: no data access yet)
if not st.session_state['authenticated']:
    st.stop()

# Logout button (per-session)
if st.sidebar.button('🚪 Logout'):
    st.session_state['authenticated'] = False
    st.rerun()

# =========================
# Sidebar status box (safe)
# =========================
def show_status_box():
    try:
        if Path(MASTER_CSV).exists():
            raw = pd.read_csv(MASTER_CSV, dtype=str)
            rows = len(raw)

            e = _to_na(raw['Eff Date']) if 'Eff Date' in raw.columns else pd.Series([], dtype=str)
            t = _to_na(raw['Term Date']) if 'Term Date' in raw.columns else pd.Series([], dtype=str)

            eff_bad = e.notna().sum() - parse_any_date(e).notna().sum() if len(e) else 0
            term_bad = t.notna().sum() - parse_any_date(t).notna().sum() if len(t) else 0

            last = _last_updated_text(MASTER_CSV)
            backup_state = "✅ Backup available" if ROLLING_BACKUP.exists() else "⚠️ No backup yet"
            msg = f'📊 {rows:,} rows • Last updated: {last} • {backup_state}'

            if eff_bad > 0 or term_bad > 0:
                msg += f' | ⚠️ Unparsed dates → Eff: {eff_bad}, Term: {term_bad}'
                st.sidebar.warning(msg)
            else:
                st.sidebar.info(msg)
        else:
            st.sidebar.warning('⚠️ Master CSV not found')
    except Exception as e:
        st.sidebar.error(f'Data temporarily unavailable: {e}')

show_status_box()

# =========================
# Load data safely
# =========================
try:
    data = _load_master_df_from_disk()
except Exception as e:
    st.error("Data failed to load. Try again shortly.")
    st.caption(str(e))
    st.stop()

# =========================
# Filters
# =========================
st.sidebar.header('Filters')

for key in ['sel_origs','sel_dests','sel_eqpts','sel_fleets']:
    if key not in st.session_state:
        st.session_state[key] = []

sel_date = st.sidebar.date_input('Select Date', value=pd.Timestamp.today().date())

# build dropdown options
orig_options = sorted([
    x for x in data['Origin'].dropna().astype(str).unique().tolist() if len(x) > 0
])
dest_options = sorted([
    x for x in data['Dest'].dropna().astype(str).unique().tolist() if len(x) > 0
])
eqpt_options = sorted([
    x for x in data['EQPT'].dropna().astype(str).unique().tolist() if len(x) > 0
])

data['Fleet'] = data['EQPT'].apply(map_to_fleet)
present_fleets = sorted([
    f for f in data['Fleet'].dropna().unique().tolist() if f in FLEET_ALLOWED
])
fleet_options = present_fleets

sel_origs = st.sidebar.multiselect(
    'Filter Origin (optional)',
    orig_options,
    default=st.session_state['sel_origs']
)
sel_dests = st.sidebar.multiselect(
    'Filter Dest (optional)',
    dest_options,
    default=st.session_state['sel_dests']
)
sel_fleets = st.sidebar.multiselect(
    'Filter Fleet (optional)',
    fleet_options,
    default=st.session_state['sel_fleets']
)
sel_eqpts = st.sidebar.multiselect(
    'Filter EQPT (optional)',
    eqpt_options,
    default=st.session_state['sel_eqpts']
)

st.session_state['sel_origs'] = sel_origs
st.session_state['sel_dests'] = sel_dests
st.session_state['sel_eqpts'] = sel_eqpts
st.session_state['sel_fleets'] = sel_fleets

if st.sidebar.button('Reset Filters'):
    for key in ['sel_origs','sel_dests','sel_eqpts','sel_fleets']:
        st.session_state[key] = []
    st.rerun()

if st.sidebar.button('🔄 Restart App', use_container_width=True):
    hard_reset()

# =========================
# Admin
# =========================
st.sidebar.markdown('---')
if 'is_admin' not in st.session_state:
    st.session_state['is_admin'] = False

if st.session_state['is_admin']:
    st.sidebar.success('✅ Admin mode enabled')
    if st.sidebar.button('Logout Admin'):
        st.session_state['is_admin'] = False
        st.rerun()

    st.sidebar.header('Upload & Merge MAP files')
    uploads = st.sidebar.file_uploader(
        'Upload Excel/CSV (first 4 rows skipped to align headers)',
        type=['xlsx','xls','csv'],
        accept_multiple_files=True
    )
    if uploads:
        st.sidebar.caption('Files queued:')
        for u in uploads:
