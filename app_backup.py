import streamlit as st
import pandas as pd
import os

MASTER_PATH = "FinalSchedule_normalized.csv"

# Canonical column order
CANON_COLS = ["Dest", "Origin", "Freq", "A/L", "EQPT", "Eff Date", "Term Date"]

# Map incoming headers to canonical names
RENAME_MAP = {
    "sta": "Dest",
    "dest": "Dest",
    "prev city": "Origin",
    "origin": "Origin",
    "freq": "Freq",
    "a/l": "A/L",
    "eqpt": "EQPT",
    "eff date": "Eff Date",
    "term date": "Term Date",
}

def normalize_cols(df: pd.DataFrame) -> pd.DataFrame:
    df.columns = [c.strip().lower() for c in df.columns]
    df = df.rename(columns={c: RENAME_MAP.get(c, c) for c in df.columns})
    keep = [c for c in CANON_COLS if c in df.columns]
    df = df[keep].copy()

    for c in ["Dest", "Origin", "Freq", "A/L", "EQPT"]:
        if c in df.columns:
            df[c] = df[c].astype(str).str.strip()

    for c in ["Eff Date", "Term Date"]:
        if c in df.columns:
            df[c] = pd.to_datetime(df[c], errors="coerce").dt.date.astype("string")
    return df

def read_map_file(upload) -> pd.DataFrame:
    name = upload.name.lower()
    if name.endswith(".xlsx"):
        df = pd.read_excel(upload, engine="openpyxl", skiprows=4, dtype=str)
    else:
        df = pd.read_csv(upload, skiprows=4, dtype=str, encoding="utf-8", on_bad_lines="skip")
    return normalize_cols(df)

def load_master() -> pd.DataFrame:
    if os.path.exists(MASTER_PATH):
        m = pd.read_csv(MASTER_PATH, dtype=str)
    else:
        m = pd.DataFrame(columns=CANON_COLS)
    m = normalize_cols(m)
    for c in CANON_COLS:
        if c not in m.columns:
            m[c] = pd.Series(dtype="string")
    return m[CANON_COLS].copy()

def merge_and_override(master: pd.DataFrame, incoming: pd.DataFrame) -> pd.DataFrame:
    combined = pd.concat([master, incoming], ignore_index=True)
    combined = combined.drop_duplicates(subset=CANON_COLS, keep="last")
    return combined.sort_values(CANON_COLS, kind="mergesort", ignore_index=True)

# ---------------------------
# Streamlit App Layout
# ---------------------------

st.set_page_config(page_title="Route Search Tool", layout="wide")
st.title("✈️ Route Search Tool")

# Upload & Merge section in sidebar
with st.sidebar.expander("➕ Upload & Merge MAP files", expanded=False):
    uploads = st.file_uploader(
        "Upload one or more MAP files (.xlsx or .csv). The first 4 rows will be removed automatically.",
        type=["xlsx", "csv"],
        accept_multiple_files=True,
        key="map_uploads"
    )

    if uploads:
        st.caption("Files to merge:")
        for u in uploads:
            st.write("•", u.name)

    if st.button("Process & Merge", type="primary", disabled=not uploads):
        master_df = load_master()

        parts, errors = [], []
        for u in uploads:
            try:
                df = read_map_file(u)
                for c in CANON_COLS:
                    if c not in df.columns:
                        df[c] = pd.Series(dtype="string")
                df = df[CANON_COLS].copy()
                parts.append(df)
            except Exception as e:
                errors.append(f"{u.name}: {e}")

        if errors:
            st.error("Some files could not be processed:\n" + "\n".join(errors))

        if parts:
            new_rows = pd.concat(parts, ignore_index=True)
            before_ct = len(master_df)
            after_merge = merge_and_override(master_df, new_rows)
            after_ct = len(after_merge)
            delta = after_ct - before_ct

            after_merge.to_csv(MASTER_PATH, index=False)

            st.success(f"Merge complete. Master rows: {before_ct:,} → {after_ct:,} (Δ {delta:+,}).")
            st.download_button(
                "Download updated FinalSchedule_normalized.csv",
                data=after_merge.to_csv(index=False).encode("utf-8"),
                file_name="FinalSchedule_normalized.csv",
                mime="text/csv"
            )
        else:
            st.warning("No valid rows found to merge.")

# ---------------------------
# Main App Functions (your existing filters, tables, maps)
# ---------------------------

st.write("🔍 Your existing filters and results tables go here...")
