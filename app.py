
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

DISPLAY_COLS = ['Dest', 'Origin', 'Freq', 'A/L', 'EQPT', 'Eff Date', 'Term Date']
FLEET_ALLOWED = ['220','320','737','757/767','764','330','350','717','RJ','Other']

# =========================
# Safe hard reset handler
# =========================
if st.session_state.get('_trigger_hard_reset_', False):
    try: st.cache_data.clear()
    except Exception: pass
    try: st.cache_resource.clear()
    except Exception: pass
    for _k in list(st.session_state.keys()):
        try: del st.session_state[_k]
        except Exception: pass
    st.rerun()

st.title('Route Search Tool')

# =========================
# Helpers
# =========================
def restart_app(full_reset: bool = True):
    st.session_state['_trigger_hard_reset_'] = True
    st.rerun()

def _to_na(s: pd.Series) -> pd.Series:
    """Treat '', 'nan', 'NaN', 'None' as NA for status math."""
    return (
        s.astype(str)
         .str.strip()
         .replace({'': np.nan, 'nan': np.nan, 'NaN': np.nan, 'None': np.nan})
    )

def parse_any_date(series: pd.Series) -> pd.Series:
    """Robust date parser: 12SEP25, 2025-09-12, 9/12/2025, Excel serials."""
    s = _to_na(series)
    # 1) ddMMMyy like 12SEP25
    dt = pd.to_datetime(s, format='%d%b%y', errors='coerce')
    # 2) generic parse
    m = dt.isna()
    if m.any():
        dt.loc[m] = pd.to_datetime(s.loc[m], errors='coerce', dayfirst=False, infer_datetime_format=True)
    # 3) Excel serials
    m = dt.isna()
    if m.any():
        as_num = pd.to_numeric(s.loc[m], errors='coerce')
        num_mask = as_num.notna()
        if num_mask.any():
            dt.loc[as_num.index[num_mask]] = pd.to_datetime(as_num[num_mask], unit='d', origin='1899-12-30', errors='coerce')
    return dt

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

def _fmt_ts(ts: float) -> str:
    # Render local time in a concise friendly format
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
def get_display_df() -> pd.DataFrame:
    if Path(MASTER_CSV).exists():
        df = pd.read_csv(MASTER_CSV, dtype=str)
        if 'Eff Date' in df.columns:
            df['Eff Date'] = parse_any_date(df['Eff Date'])
        if 'Term Date' in df.columns:
            df['Term Date'] = parse_any_date(df['Term Date'])
        df = ensure_display_cols(df)
        df = clean_origin(df)
        return df
    else:
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
            df = df.rename(columns=rename_map)
            if 'Eff Date' in df.columns:
                df['Eff Date'] = parse_any_date(df['Eff Date'])
            if 'Term Date' in df.columns:
                df['Term Date'] = parse_any_date(df['Term Date'])
            df = ensure_display_cols(df)
            df = clean_origin(df)
            return df
        else:
            base = pd.DataFrame(columns=DISPLAY_COLS)
            base.to_csv(MASTER_CSV, index=False)
            return base

def read_map_upload(file_like) -> pd.DataFrame:
    """Read uploaded MAP .xlsx/.csv, skip first 4 rows. Skip rows with blank/invalid dates or blank Origin."""
    name = getattr(file_like, 'name', '').lower()
    if name.endswith('.xlsx') or name.endswith('.xls'):
        df = pd.read_excel(file_like, header=0, skiprows=4, dtype=str, engine='openpyxl')
    else:
        df = pd.read_csv(file_like, header=0, skiprows=4, dtype=str, encoding='utf-8', on_bad_lines='skip')
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
    df = ensure_display_cols(df)

    # Strip text
    for c in ['Dest','Origin','Freq','A/L','EQPT']:
        df[c] = df[c].astype(str).str.strip()

    # Robust dates
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
        if Path(MASTER_CSV).exists():
            backups_dir = Path('backups')
            backups_dir.mkdir(exist_ok=True)
            ts = pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')
            backup_path = backups_dir / f'FinalSchedule_normalized_{ts}.csv'
            shutil.copy(MASTER_CSV, backup_path)
            return backup_path
    except Exception as e:
        st.sidebar.warning(f'Backup skipped: {e}')
    return None

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

def merge_override(master_df: pd.DataFrame, new_df: pd.DataFrame) -> pd.DataFrame:
    combined = pd.concat([master_df, new_df], ignore_index=True)
    combined = ensure_display_cols(combined)
    combined = combined.drop_duplicates(subset=DISPLAY_COLS, keep='last')
    combined = combined.sort_values(by=DISPLAY_COLS, kind='mergesort', ignore_index=True)
    return combined

# ---- Fleet categorization ----
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

# ---- Cleaner for MASTER ----
def clean_master_df(df: pd.DataFrame):
    df = ensure_display_cols(df)
    # strip text cols
    for c in ['Dest','Origin','Freq','A/L','EQPT']:
        df[c] = df[c].astype(str).str.strip()
    eff = parse_any_date(df['Eff Date'])
    term = parse_any_date(df['Term Date'])
    origin_ok = df['Origin'].astype(str).str.strip().ne('')
    dates_ok = eff.notna() & term.notna()
    keep = dates_ok & origin_ok
    dropped_dates = int((~dates_ok).sum())
    dropped_origin = int((~origin_ok).sum())
    dropped_total = int((~keep).sum())
    cleaned = df.loc[keep].copy()
    cleaned['Eff Date'] = eff.loc[keep].dt.strftime('%Y-%m-%d')
    cleaned['Term Date'] = term.loc[keep].dt.strftime('%Y-%m-%d')
    cleaned = cleaned.drop_duplicates(subset=DISPLAY_COLS, keep='last')
    cleaned = cleaned.sort_values(by=DISPLAY_COLS, kind='mergesort', ignore_index=True)
    return cleaned, dropped_total, dropped_dates, dropped_origin

# =========================
# Auth
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
if not st.session_state['authenticated']:
    st.stop()

if st.sidebar.button('🚪 Logout'):
    st.session_state['authenticated'] = False
    st.rerun()

# =========================
# Status box + Last updated
# =========================
def show_status_box():
    try:
        if Path(MASTER_CSV).exists():
            raw = pd.read_csv(MASTER_CSV, dtype=str)
            rows = len(raw)
            eff_bad = term_bad = 0
            if 'Eff Date' in raw.columns:
                e = _to_na(raw['Eff Date'])
                eff_bad = e.notna().sum() - parse_any_date(e).notna().sum()
            if 'Term Date' in raw.columns:
                t = _to_na(raw['Term Date'])
                term_bad = t.notna().sum() - parse_any_date(t).notna().sum()
            msg = f'📊 Master CSV loaded: {rows:,} rows'
            last = _last_updated_text(MASTER_CSV)
            if last and last != '—':
                msg += f' • Last updated: {last}'
            if eff_bad > 0 or term_bad > 0:
                msg += f' | ⚠️ Unparsed dates → Eff Date: {eff_bad}, Term Date: {term_bad}'
                st.sidebar.warning(msg)
            else:
                st.sidebar.info(msg)
        else:
            st.sidebar.warning('⚠️ Master CSV not found')
    except Exception as e:
        st.sidebar.error(f'Failed to read master CSV: {e}')

show_status_box()
data = get_display_df()

# =========================
# Sidebar Filters
# =========================
st.sidebar.header('Filters')
if 'sel_origs' not in st.session_state: st.session_state['sel_origs'] = []
if 'sel_dests' not in st.session_state: st.session_state['sel_dests'] = []
if 'sel_eqpts' not in st.session_state: st.session_state['sel_eqpts'] = []
if 'sel_fleets' not in st.session_state: st.session_state['sel_fleets'] = []

sel_date = st.sidebar.date_input('Select Date', value=pd.Timestamp.today().date())
orig_options = sorted([x for x in data['Origin'].dropna().astype(str).unique().tolist() if len(x) > 0])
dest_options = sorted([x for x in data['Dest'].dropna().astype(str).unique().tolist() if len(x) > 0])
eqpt_options = sorted([x for x in data['EQPT'].dropna().astype(str).unique().tolist() if len(x) > 0])

data['Fleet'] = data['EQPT'].apply(map_to_fleet)
present_fleets = sorted([f for f in data['Fleet'].dropna().unique().tolist() if f in FLEET_ALLOWED])
fleet_options = present_fleets

sel_origs = st.sidebar.multiselect('Filter Origin (optional)', orig_options, default=st.session_state['sel_origs'])
sel_dests = st.sidebar.multiselect('Filter Dest (optional)', dest_options, default=st.session_state['sel_dests'])
sel_fleets = st.sidebar.multiselect('Filter Fleet (optional)', fleet_options, default=st.session_state['sel_fleets'])
sel_eqpts = st.sidebar.multiselect('Filter EQPT (optional)', eqpt_options, default=st.session_state['sel_eqpts'])

st.session_state['sel_origs'] = sel_origs
st.session_state['sel_dests'] = sel_dests
st.session_state['sel_eqpts'] = sel_eqpts
st.session_state['sel_fleets'] = sel_fleets

if st.sidebar.button('Reset Filters'):
    st.session_state['sel_origs'] = []
    st.session_state['sel_dests'] = []
    st.session_state['sel_eqpts'] = []
    st.session_state['sel_fleets'] = []
    st.rerun()

if st.sidebar.button('🔄 Restart App', use_container_width=True):
    restart_app(full_reset=True)

# =========================
# Admin section
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
            st.sidebar.write('•', u.name)

    if st.sidebar.button('Process & Merge', type='primary', disabled=not uploads):
        backup_master()
        master = pd.read_csv(MASTER_CSV, dtype=str) if Path(MASTER_CSV).exists() else pd.DataFrame(columns=DISPLAY_COLS)
        master = ensure_display_cols(master)

        parts, errors = [], []
        for up in uploads or []:
            try:
                dfp = read_map_upload(up)
                parts.append(dfp)
            except Exception as e:
                errors.append(f"{getattr(up,'name','file')}: {e}")

        if errors:
            st.sidebar.error("Some files failed:\n" + "\n".join(errors))

        if parts:
            incoming = pd.concat(parts, ignore_index=True)
            merged = merge_override(master, incoming)

            # Extra safety: clean merged before saving
            cleaned, dropped_total, dropped_dates, dropped_origin = clean_master_df(merged)
            if dropped_total > 0:
                st.sidebar.warning(f'Post-merge cleanup dropped {dropped_total} rows (invalid/missing dates: {dropped_dates}, blank origin: {dropped_origin}).')
            cleaned.to_csv(MASTER_CSV, index=False)

            st.sidebar.success(f'Merge complete. Rows now: {len(cleaned):,}')
            restart_app(full_reset=True)
        else:
            st.sidebar.warning('No valid rows found to merge.')

    st.sidebar.markdown('---')
    st.sidebar.subheader('Maintenance')
    if st.sidebar.button('⏪ Restore latest backup', use_container_width=True):
        restore_latest_backup()

    if st.sidebar.button('🧹 Clean & normalize master (drop invalid dates/origin)', use_container_width=True):
        try:
            backup_master()
            if Path(MASTER_CSV).exists():
                raw = pd.read_csv(MASTER_CSV, dtype=str)
            else:
                raw = pd.DataFrame(columns=DISPLAY_COLS)
            cleaned, dropped_total, dropped_dates, dropped_origin = clean_master_df(raw)
            cleaned.to_csv(MASTER_CSV, index=False)
            st.sidebar.success(f'Cleaned master. Dropped {dropped_total} rows (invalid/missing dates: {dropped_dates}, blank origin: {dropped_origin}). Now {len(cleaned):,} rows.')
            restart_app(full_reset=True)
        except Exception as e:
            st.sidebar.error(f'Clean failed: {e}')

    # --- Clear All Data runs cleaner (last step) ---
    _confirm_clear = st.sidebar.checkbox('Confirm delete all data')
    _btn_clear_all = st.sidebar.button('Clear All Data')
    if _btn_clear_all and _confirm_clear:
        try:
            # 1) Backup current master
            backup_master()
            # 2) Write empty master with headers only
            pd.DataFrame(columns=DISPLAY_COLS).to_csv(MASTER_CSV, index=False)
            # 3) Run the cleaner (no-op on empty, but ensures normalization pipeline executes)
            raw = pd.read_csv(MASTER_CSV, dtype=str) if Path(MASTER_CSV).exists() else pd.DataFrame(columns=DISPLAY_COLS)
            cleaned, _drop_total, _drop_dates, _drop_origin = clean_master_df(raw)
            cleaned.to_csv(MASTER_CSV, index=False)
            st.sidebar.success('All data cleared and master normalized.')
            restart_app(full_reset=True)
        except Exception as e:
            st.sidebar.error('Failed to clear & normalize: ' + str(e))

else:
    admin_pass = st.sidebar.text_input('Admin Password', type='password')
    if admin_pass == ADMIN_PASSWORD:
        st.session_state['is_admin'] = True
        st.rerun()
    else:
        st.sidebar.info('🔒 Admin mode locked — enter password to access upload & maintenance')

# =========================
# Filtering
# =========================
df = data.copy()
sel_ts = pd.Timestamp(sel_date)
df['Fleet'] = df['EQPT'].apply(map_to_fleet)

if 'Eff Date' in df.columns and 'Term Date' in df.columns:
    df['Eff Date'] = parse_any_date(df['Eff Date'])
    df['Term Date'] = parse_any_date(df['Term Date'])
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
if len(sel_fleets) > 0:
    df = df[df['Fleet'].isin(sel_fleets)]
if len(sel_eqpts) > 0:
    df = df[df['EQPT'].astype(str).isin(sel_eqpts)]

# =========================
# Unique Destinations (wide table)
# =========================
def render_unique_dest_table(filtered_df: pd.DataFrame, n_cols: int = 7, height: int = 220):
    st.subheader('Unique Destinations')
    if filtered_df.empty:
        st.info('No data for current filters.')
        return []
    dest_col_candidates = [c for c in filtered_df.columns if c.strip().lower() in ('dest','dest (sta)','sta','destination')]
    dest_col = dest_col_candidates[0] if dest_col_candidates else 'Dest'
    uniq = (
        filtered_df[dest_col]
        .astype(str)
        .str.strip()
        .replace({'nan': np.nan, 'None': np.nan, '': np.nan})
        .dropna()
        .unique()
    )
    uniq = np.sort(uniq)
    n = len(uniq)
    if n == 0:
        st.info('No unique destinations found.')
        return []
    rows = int(np.ceil(n / n_cols))
    table = np.empty((rows, n_cols), dtype=object)
    table[:] = ''
    for i, val in enumerate(uniq):
        r = i // n_cols
        c = i % n_cols
        table[r, c] = val
    wide_df = pd.DataFrame(table, columns=[f'Dest {i+1}' for i in range(n_cols)])
    st.dataframe(wide_df, use_container_width=True, height=height)
    return list(uniq)

unique_list = render_unique_dest_table(df, n_cols=7, height=220)

# =========================
# Results Table
# =========================
st.subheader('Filtered Results')
st.write(f'Date: {sel_date} | Rows: {len(df)}')
show_cols = ['Dest', 'Origin', 'Freq', 'A/L', 'EQPT', 'Fleet', 'Eff Date', 'Term Date']
for c in show_cols:
    if c not in df.columns:
        df[c] = pd.NA
st.dataframe(df[show_cols], use_container_width=True, height=420)
st.caption('Showing: Dest, Origin, Freq, A/L, EQPT, Fleet, Eff Date, Term Date')

# =========================
# Map of Unique Destinations
# =========================
@st.cache_data
def load_airports(path: str):
    if not Path(path).exists():
        return pd.DataFrame(columns=['Dest','Lat','Long'])
    a = pd.read_csv(path, dtype={'Dest': str, 'Lat': float, 'Long': float})
    a['Dest'] = a['Dest'].str.strip()
    return a

st.subheader('Map of Unique Destinations')
def render_map(unique_dests: list):
    if not unique_dests:
        st.info('No destinations to plot.')
        return
    airports_df = load_airports(IATA_LATLONG_CSV)
    points = pd.DataFrame({'Dest': unique_dests}).merge(
        airports_df, on='Dest', how='left'
    ).dropna(subset=['Lat','Long'])
    if points.empty:
        st.info('No coordinates available for current selection.')
        return
    deck = pdk.Deck(
        layers=[
            pdk.Layer(
                'ScatterplotLayer',
                data=points,
                get_position='[Long, Lat]',
                get_radius=80000,
                get_fill_color=[0, 120, 220, 160],
                pickable=True,
            )
        ],
        initial_view_state=pdk.ViewState(
            latitude=float(points['Lat'].mean()) if not points['Lat'].isna().all() else 39.0,
            longitude=float(points['Long'].mean()) if not points['Long'].isna().all() else -98.0,
            zoom=3,
            pitch=0,
            bearing=0,
        ),
        tooltip={'text': 'Destination: {Dest}'},
        map_provider='mapbox',
        map_style=None,
    )
    st.pydeck_chart(deck)

render_map(unique_list)
