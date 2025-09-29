import os
from pathlib import Path
import pandas as pd
import shutil
import streamlit as st

# --- Config ---
st.set_page_config(page_title='Route Search Tool', layout='wide')
st.title('Route Search Tool')

# --- Restart Helper ---
def restart_app(full_reset: bool = True):
    # Clear Streamlit caches
    try:
        st.cache_data.clear()
    except Exception:
        pass
    try:
        st.cache_resource.clear()
    except Exception:
        pass
    # Optionally clear session state to reset filters/UI
    if full_reset:
        for k in list(st.session_state.keys()):
            try:
                del st.session_state[k]
            except Exception:
                pass
    st.rerun()


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

# Filenames (can be overridden via environment if using docker-compose)
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

def load_raw_excel(path: str) -> pd.DataFrame:
    xl = pd.ExcelFile(path)
    sheet = xl.sheet_names[0]
    raw = pd.read_excel(path, sheet_name=sheet, header=None)
    header_idx = None
    for i in range(min(25, len(raw))):
        row_vals = raw.iloc[i].astype(str).str.strip().str.upper().tolist()
        if ('STA' in row_vals) and (('PREV CITY' in row_vals) or ('PREV  CITY' in row_vals) or ('PREVCITY' in row_vals)):
            header_idx = i
            break
    if header_idx is None:
        header_idx = 0
    df = pd.read_excel(path, sheet_name=sheet, header=header_idx)
    df.columns = [str(c).strip() for c in df.columns]
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
        if Path(DATA_XLSX).exists():
            df = load_raw_excel(DATA_XLSX)
            df = df.rename(columns=RENAME_MAP)
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

def read_uploaded_excel(file_like) -> pd.DataFrame:
    xl = pd.ExcelFile(file_like)
    sheet = xl.sheet_names[0]
    temp = pd.read_excel(file_like, sheet_name=sheet, header=None)
    header_idx = None
    for i in range(min(25, len(temp))):
        row_vals = temp.iloc[i].astype(str).str.strip().str.upper().tolist()
        if ('STA' in row_vals) and (('PREV CITY' in row_vals) or ('PREV  CITY' in row_vals) or ('PREVCITY' in row_vals)):
            header_idx = i
            break
    if header_idx is None:
        header_idx = 0
    df = pd.read_excel(file_like, sheet_name=sheet, header=header_idx)
    df.columns = [str(c).strip() for c in df.columns]

    col_map = {}
    for c in df.columns:
        cname = str(c).strip().upper().replace("  ", " ")
        if cname == "STA":
            col_map[c] = "Dest"
        elif cname in ["PREV CITY", "PREV  CITY", "PREVCITY"]:
            col_map[c] = "Origin"
        elif cname == "EFF DATE":
            col_map[c] = "Eff Date"
        elif cname == "TERM DATE":
            col_map[c] = "Term Date"
        elif cname == "FREQ":
            col_map[c] = "Freq"
        elif cname == "A/L":
            col_map[c] = "A/L"
        elif cname == "EQPT":
            col_map[c] = "EQPT"

    df = df.rename(columns=col_map)
    if 'Eff Date' in df.columns:
        df['Eff Date'] = parse_dates(df['Eff Date'])
    if 'Term Date' in df.columns:
        df['Term Date'] = parse_dates(df['Term Date'])
    df = ensure_display_cols(df)
    df = clean_origin(df)
    return df

@st.cache_data(show_spinner=False)
def make_key_ui(df: pd.DataFrame) -> pd.Series:
    temp = df.copy()
    for c in ['Dest','Origin','Freq','A/L','EQPT']:
        if c in temp.columns:
            temp[c] = temp[c].astype(str).str.strip()
    if 'Eff Date' in temp.columns:
        temp['Eff Date'] = parse_dates(temp['Eff Date']).dt.strftime('%Y-%m-%d')
    if 'Term Date' in temp.columns:
        temp['Term Date'] = parse_dates(temp['Term Date']).dt.strftime('%Y-%m-%d')
    key = (
        temp['Dest'].fillna('').astype(str) + '|' +
        temp['Origin'].fillna('').astype(str) + '|' +
        temp['Freq'].fillna('').astype(str) + '|' +
        temp['A/L'].fillna('').astype(str) + '|' +
        temp['EQPT'].fillna('').astype(str) + '|' +
        temp['Eff Date'].fillna('').astype(str) + '|' +
        temp['Term Date'].fillna('').astype(str)
    )
    return key

def handle_upload(upload) -> None:
    try:
        new_df = read_uploaded_excel(upload)
        st.info('Upload parsed rows: ' + str(len(new_df)))
        st.write(new_df.head())

        master = pd.read_csv(MASTER_CSV) if Path(MASTER_CSV).exists() else pd.DataFrame(columns=DISPLAY_COLS)
        if 'Eff Date' in master.columns:
            master['Eff Date'] = parse_dates(master['Eff Date'])
        if 'Term Date' in master.columns:
            master['Term Date'] = parse_dates(master['Term Date'])
        master = ensure_display_cols(master)
        master = clean_origin(master)

        master_key = set(make_key_ui(master).tolist()) if len(master) > 0 else set()
        new_key = make_key_ui(new_df)
        mask_new = ~new_key.isin(master_key)
        to_add = new_df[mask_new].copy()

        st.info('New rows detected: ' + str(len(to_add)))
        st.write(to_add.head())

        if len(to_add) > 0:
            combined = pd.concat([master, to_add], ignore_index=True)
            if 'Eff Date' in combined.columns:
                combined['Eff Date'] = parse_dates(combined['Eff Date']).dt.strftime('%Y-%m-%d')
            if 'Term Date' in combined.columns:
                combined['Term Date'] = parse_dates(combined['Term Date']).dt.strftime('%Y-%m-%d')
            combined = ensure_display_cols(combined)
            combined = clean_origin(combined)
            try:
                # Backup current master before overwriting
                if Path(MASTER_CSV).exists():
                    backups_dir = Path('backups')
                    backups_dir.mkdir(exist_ok=True)
                    ts = pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')
                    backup_path = backups_dir / f'FinalSchedule_normalized_{ts}.csv'
                    shutil.copy(MASTER_CSV, backup_path)
            except Exception as _bkp_err:
                st.sidebar.warning(f'Backup skipped: {_bkp_err}')
            combined.to_csv(MASTER_CSV, index=False)
            st.success(f'Added {len(to_add)} new records. Master CSV updated.')
            st.info("✅ Please click '🔄 Restart App' in the sidebar to reload the updated database.")
        else:
            st.warning('No new records to add. Master may already contain these rows after normalization.')
            st.info("If you believe new data should be visible, click '🔄 Restart App' in the sidebar to force reload.")

        show_status_box()
    except Exception as e:
        st.error('Upload failed: ' + str(e))

def handle_clear_all(confirm: bool, trigger: bool) -> None:
    if trigger and confirm:
        try:
            pd.DataFrame(columns=DISPLAY_COLS).to_csv(MASTER_CSV, index=False)
            st.sidebar.success('All data cleared. Master reset to headers only.')
            show_status_box()
        except Exception as e:
            st.sidebar.error('Failed to clear data: ' + str(e))

def restore_latest_backup():
    try:
        backups_dir = Path('backups')
        if not backups_dir.exists():
            st.sidebar.error('No backups folder found.')
            return
        backups = sorted(backups_dir.glob('FinalSchedule_normalized_*.csv'))
        if not backups:
            st.sidebar.error('No backup files found.')
            return
        latest = backups[-1]
        shutil.copy(latest, MASTER_CSV)
        st.sidebar.success(f'Restored: {latest.name} → {MASTER_CSV}')
        restart_app(full_reset=True)
    except Exception as e:
        st.sidebar.error('Restore failed: ' + str(e))



# ---------- MAP Merge Utilities (added) ----------
def _normalize_map_df(df: pd.DataFrame) -> pd.DataFrame:
    """Keep only the 7 display columns with standardized names and types."""
    # Unify headers to our canonical names
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
    df = df.rename(columns={c: mapping.get(str(c).strip(), c) for c in df.columns})
    df = ensure_display_cols(df)

    # Strip spaces
    for c in ['Dest','Origin','Freq','A/L','EQPT']:
        df[c] = df[c].astype(str).str.strip()

    # Parse dates; keep as string YYYY-MM-DD for stable dedupe/CSV
    df['Eff Date'] = parse_dates(df['Eff Date']).dt.strftime('%Y-%m-%d')
    df['Term Date'] = parse_dates(df['Term Date']).dt.strftime('%Y-%m-%d')
    return df[DISPLAY_COLS].copy()

def _read_map_file_generic(file_like) -> pd.DataFrame:
    """Read .xlsx or .csv MAP file, skipping first 4 rows (to drop preamble)."""
    name = getattr(file_like, 'name', 'uploaded')
    lower = str(name).lower()
    if lower.endswith('.xlsx') or lower.endswith('.xls'):
        df = pd.read_excel(file_like, header=0, skiprows=4, dtype=str)
    else:
        df = pd.read_csv(file_like, header=0, skiprows=4, dtype=str, encoding='utf-8', on_bad_lines='skip')
    # Clean column names
    df.columns = [str(c).strip() for c in df.columns]
    return _normalize_map_df(df)

def merge_override(master_df: pd.DataFrame, new_df: pd.DataFrame) -> pd.DataFrame:
    """Override duplicates based on full-row match across DISPLAY_COLS."""
    combined = pd.concat([master_df, new_df], ignore_index=True)
    combined = ensure_display_cols(combined)
    # Drop duplicates keeping the last (so uploaded rows win)
    combined = combined.drop_duplicates(subset=DISPLAY_COLS, keep='last')
    # Sort for stable display
    combined = combined.sort_values(by=DISPLAY_COLS, kind='mergesort', ignore_index=True)
    return combined


# ---------- Main App ----------
data = get_display_df()
orig_options = sorted([x for x in data['Origin'].dropna().astype(str).unique().tolist() if len(x) > 0])
dest_options = sorted([x for x in data['Dest'].dropna().astype(str).unique().tolist() if len(x) > 0])
eqpt_options = sorted([x for x in data['EQPT'].dropna().astype(str).unique().tolist() if len(x) > 0])

st.sidebar.header('Filters')
if "sel_origs" not in st.session_state: st.session_state["sel_origs"] = []
if "sel_dests" not in st.session_state: st.session_state["sel_dests"] = []
if "sel_eqpts" not in st.session_state: st.session_state["sel_eqpts"] = []

sel_date = st.sidebar.date_input('Select Date', value=pd.Timestamp.today().date())
sel_origs = st.sidebar.multiselect('Filter Origin (optional)', orig_options, default=st.session_state["sel_origs"])
sel_dests = st.sidebar.multiselect('Filter Dest (optional)', dest_options, default=st.session_state["sel_dests"])
sel_eqpts = st.sidebar.multiselect('Filter EQPT (optional)', eqpt_options, default=st.session_state["sel_eqpts"])
st.session_state["sel_origs"] = sel_origs
st.session_state["sel_dests"] = sel_dests
st.session_state["sel_eqpts"] = sel_eqpts

if st.sidebar.button("Reset Filters"):
    st.session_state["sel_origs"] = []
    st.session_state["sel_dests"] = []
    st.session_state["sel_eqpts"] = []
    st.rerun()

if st.sidebar.button("🔄 Restart App"):
    restart_app(full_reset=True)

st.sidebar.markdown("---")
if "is_admin" not in st.session_state:
    st.session_state["is_admin"] = False

if st.session_state["is_admin"]:
    st.sidebar.success("✅ Admin mode enabled")
    if st.sidebar.button("Logout Admin"):
        st.session_state["is_admin"] = False
        st.rerun()

    st.sidebar.header('Upload New Excel')
    upload = st.sidebar.file_uploader('Upload Excel', type=['xlsx','xls'])
    if upload is not None:
        handle_upload(upload)

    st.sidebar.markdown('---')
    st.sidebar.subheader('Maintenance')
    if st.sidebar.button('⏪ Restore latest backup', use_container_width=True):
        restore_latest_backup()
    _confirm_clear = st.sidebar.checkbox('Confirm delete all data')
    _btn_clear_all = st.sidebar.button('Clear All Data')
    handle_clear_all(_confirm_clear, _btn_clear_all)
else:
    admin_pass = st.sidebar.text_input("Admin Password", type="password")
    if admin_pass == ADMIN_PASSWORD:
        st.session_state["is_admin"] = True
        st.rerun()
    else:
        st.sidebar.error("🔒 Admin mode locked — enter password to access upload & maintenance")

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

if len(sel_dests) > 0:
    df = df[df['Dest'].astype(str).isin(sel_dests)]
if len(sel_origs) > 0:
    df = df[df['Origin'].astype(str).isin(sel_origs)]
if len(sel_eqpts) > 0:
    df = df[df['EQPT'].astype(str).isin(sel_eqpts)]

st.subheader('Filtered Results')
st.write('Date: ' + str(sel_date) + ' | Rows: ' + str(len(df)))
st.dataframe(df, width='stretch')
st.caption('Showing only columns: Dest, Origin, Freq, A/L, EQPT, Eff Date, Term Date')
