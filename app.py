import streamlit as st
import pandas as pd
import numpy as np
import re
import io
from datetime import datetime

pd.set_option("styler.render.max_elements", 50_000_000)

st.set_page_config(page_title="HYP vs BW Check", layout="wide", page_icon="🔍")

PAGE_SIZE = 500  # rows shown per page

# ──────────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────────

DAT_COLUMNS = [
    "Scenario", "Year", "Period", "View",
    "Entity", "Value", "Account", "ICP",
    "Custom1", "Custom2", "Custom3", "Custom4", "Amount",
]

JOIN_KEYS: list[tuple[str, str]] = [
    ("Scenario", "/BIC/SCNRBI"),
    ("Year",     "CALYEAR"),
    ("Period",   "MONTH"),
    ("Entity",   "/BIC/COMPBI"),
    ("Account",  "/BIC/ACCOUNTBI"),
    ("ICP",      "/BIC/ICPBI"),
    ("Custom1",  "/BIC/CUSTOM1BI"),
    ("Custom2",  "/BIC/CUSTOM2BI"),
    ("Custom3",  "/BIC/CUSTOM3BI"),
    ("Custom4",  "/BIC/CUSTOM4BI"),
]

NUMERIC_MAP = [("Amount", "CUM_VALUE")]

MONTH_MAP = {
    "jan":"1","feb":"2","mar":"3","apr":"4","may":"5","jun":"6",
    "jul":"7","aug":"8","sep":"9","oct":"10","nov":"11","dec":"12",
}
_NONE_RE = re.compile(r"^\[?(icp\s*)?none\]?$", re.IGNORECASE)

STATUS_COLORS = {
    "HYP only — not in BW":  "#FFA500",
    "BW only — not in HYP":  "#ADD8E6",
    "Sign difference only":   "#FFFF66",
    "Value mismatch":         "#FF9999",
}

# ──────────────────────────────────────────────
# NORMALISATION
# ──────────────────────────────────────────────

def norm_period(s: pd.Series) -> pd.Series:
    def _f(v):
        v = str(v).strip()
        lo = v.lower()
        if lo in MONTH_MAP:
            return MONTH_MAP[lo]
        try:
            return str(int(float(v)))
        except Exception:
            return v
    return s.map(_f)

def norm_icp(s: pd.Series) -> pd.Series:
    def _f(v):
        v = str(v).strip()
        return "NONE" if _NONE_RE.match(v) else v.strip("[]").strip()
    return s.map(_f)

def normalise(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    df = df.copy()
    for col in cols:
        if col not in df.columns:
            continue
        df[col] = df[col].astype(str).str.strip().str.upper()
        if col in ("Period", "MONTH"):
            df[col] = norm_period(df[col])
        if col in ("ICP", "/BIC/ICPBI"):
            df[col] = norm_icp(df[col])
    return df

# ──────────────────────────────────────────────
# FILE LOADERS
# ──────────────────────────────────────────────

@st.cache_data(show_spinner=False)
def load_dat(file_bytes: bytes) -> pd.DataFrame:
    df = pd.read_csv(
        io.BytesIO(file_bytes),
        encoding="utf-16", sep=";", skiprows=1,
        header=None, names=DAT_COLUMNS, dtype=str, on_bad_lines="warn",
    )
    df = df.apply(lambda c: c.str.strip() if c.dtype == object else c)
    return df.dropna(how="all").reset_index(drop=True)

@st.cache_data(show_spinner=False)
def load_bw(file_bytes: bytes) -> pd.DataFrame:
    bw_exp = {"/BIC/ACCOUNTBI", "/BIC/COMPBI", "/BIC/SCNRBI", "CALYEAR", "CUM_VALUE"}
    df0 = pd.read_excel(io.BytesIO(file_bytes), dtype=str, header=0)
    df0.columns = df0.columns.str.strip()
    df1 = pd.read_excel(io.BytesIO(file_bytes), dtype=str, header=1)
    df1.columns = df1.columns.str.strip()
    df = df1 if len(bw_exp & set(df1.columns)) >= len(bw_exp & set(df0.columns)) else df0
    df = df.apply(lambda c: c.str.strip() if c.dtype == object else c)
    return df.dropna(how="all").reset_index(drop=True)

# ──────────────────────────────────────────────
# COMPARISON
# ──────────────────────────────────────────────

def compare(hyp: pd.DataFrame, bw: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    hyp_keys = [h for h, _ in JOIN_KEYS]
    bw_keys  = [b for _, b in JOIN_KEYS]

    missing_hyp = [c for c in hyp_keys if c not in hyp.columns]
    missing_bw  = [c for c in bw_keys  if c not in bw.columns]

    diag = {
        "missing_hyp": missing_hyp,
        "missing_bw":  missing_bw,
        "key_overlaps": {},
    }

    if missing_hyp or missing_bw:
        return pd.DataFrame(), diag

    hyp_c = normalise(hyp, hyp_keys)
    bw_c  = normalise(bw,  bw_keys)

    # Key overlap diagnostics
    for h_col, b_col in JOIN_KEYS:
        hyp_vals = set(hyp_c[h_col].dropna().unique())
        bw_vals  = set(bw_c[b_col].dropna().unique())
        overlap  = hyp_vals & bw_vals
        diag["key_overlaps"][h_col] = {
            "hyp_unique": len(hyp_vals),
            "bw_unique":  len(bw_vals),
            "overlap":    len(overlap),
            "sample_hyp": sorted(hyp_vals)[:5],
            "sample_bw":  sorted(bw_vals)[:5],
        }

    bw_c = bw_c.rename(columns={b: h for h, b in JOIN_KEYS})
    bw_c = bw_c.rename(columns={b: f"{h}_BW" for h, b in NUMERIC_MAP})
    bw_keep = hyp_keys + [f"{h}_BW" for h, _ in NUMERIC_MAP]
    bw_c = bw_c[[c for c in bw_keep if c in bw_c.columns]]

    merged = hyp_c.merge(bw_c, on=hyp_keys, how="outer", indicator=True)
    diag["matched_rows"] = (merged["_merge"] == "both").sum()
    diag["hyp_only_rows"] = (merged["_merge"] == "left_only").sum()
    diag["bw_only_rows"]  = (merged["_merge"] == "right_only").sum()

    results = []
    for hyp_col, bw_col_orig in NUMERIC_MAP:
        bw_col = f"{hyp_col}_BW"
        if hyp_col not in merged.columns or bw_col not in merged.columns:
            continue

        hyp_vals = pd.to_numeric(merged[hyp_col].astype(str).str.strip(), errors="coerce")
        bw_vals  = pd.to_numeric(merged[bw_col].astype(str).str.strip(),  errors="coerce")
        diff     = hyp_vals - bw_vals

        # Exclude rows where both values are zero — only keep non-zero amounts
        nonzero  = (hyp_vals.abs() > 1e-4) | (bw_vals.abs() > 1e-4)
        mismatch = nonzero & ((merged["_merge"] != "both") | (diff.abs() > 1e-4))

        sub = merged.loc[mismatch, hyp_keys].copy()
        sub[f"HYP_Amount"]           = hyp_vals[mismatch].values
        sub[f"BW_CUM_VALUE"]         = bw_vals[mismatch].values
        sub["Difference (HYP-BW)"]   = diff[mismatch].values
        sub["Abs Difference"]         = diff[mismatch].abs().values

        hyp_m = hyp_vals[mismatch].values
        bw_m  = bw_vals[mismatch].values
        flags = merged.loc[mismatch, "_merge"].values
        statuses = []
        for flag, h, b in zip(flags, hyp_m, bw_m):
            if flag == "left_only":
                statuses.append("HYP only — not in BW")
            elif flag == "right_only":
                statuses.append("BW only — not in HYP")
            elif not np.isnan(h) and not np.isnan(b) and abs(abs(h) - abs(b)) <= 1e-4:
                statuses.append("Sign difference only")
            else:
                statuses.append("Value mismatch")
        sub["Status"] = statuses
        results.append(sub)

    result = pd.concat(results, ignore_index=True) if results else pd.DataFrame()
    return result, diag, hyp_c, bw_c

# ──────────────────────────────────────────────
# EXCEL EXPORT
# ──────────────────────────────────────────────

def to_excel_bytes(df: pd.DataFrame) -> bytes:
    from openpyxl.styles import PatternFill, Font
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Differences", index=False)
        ws = writer.sheets["Differences"]
        for col_cells in ws.columns:
            w = max(len(str(c.value or "")) for c in col_cells) + 2
            ws.column_dimensions[col_cells[0].column_letter].width = min(w, 45)
        for cell in ws[1]:
            cell.font = Font(bold=True)
        status_idx = list(df.columns).index("Status")
        for row in ws.iter_rows(min_row=2, max_row=ws.max_row):
            colour = STATUS_COLORS.get(row[status_idx].value, "").lstrip("#")
            if colour:
                fill = PatternFill(fill_type="solid", fgColor=colour)
                for cell in row:
                    cell.fill = fill
    return buf.getvalue()

# ──────────────────────────────────────────────
# UI
# ──────────────────────────────────────────────

st.title("🔍 HYP vs BW Comparison Tool")

col1, col2 = st.columns(2)

with col1:
    st.subheader("📂 Hyperion .dat file")
    dat_file = st.file_uploader("Upload .dat file", type=["dat", "txt", "csv"], key="dat")

with col2:
    st.subheader("📊 BW Excel backup file")
    bw_file = st.file_uploader("Upload BW Excel file", type=["xlsx", "xls"], key="bw")

if dat_file and bw_file:
    with st.spinner("Loading files…"):
        hyp_df = load_dat(dat_file.read())
        bw_df  = load_bw(bw_file.read())

    col1, col2 = st.columns(2)
    with col1:
        st.success(f"✅ HYP loaded — {len(hyp_df):,} rows")
        with st.expander("Preview HYP data"):
            st.dataframe(hyp_df.head(10), use_container_width=True)
    with col2:
        st.success(f"✅ BW loaded — {len(bw_df):,} rows")
        with st.expander("Preview BW data"):
            st.dataframe(bw_df.head(10), use_container_width=True)

    st.divider()
    if st.button("▶️ Run Comparison", type="primary", use_container_width=True):
        with st.spinner("Comparing…"):
            result, diag, hyp_norm, bw_norm = compare(hyp_df, bw_df)
        st.session_state["result"]   = result
        st.session_state["diag"]     = diag
        st.session_state["hyp_norm"] = hyp_norm   # normalised HYP for drill-down
        st.session_state["bw_norm"]  = bw_norm    # normalised BW  for drill-down
        st.session_state["page"]     = 1
        st.session_state.pop("selected_row", None)

    # ── Show results (persists across filter reruns) ──────────────
    if "result" in st.session_state:
        result = st.session_state["result"]
        diag   = st.session_state["diag"]

        # ── Diagnostics ──────────────────────────────────────────
        st.subheader("🔬 Join Key Diagnostics")
        if diag.get("missing_hyp"):
            st.error(f"HYP file missing columns: {diag['missing_hyp']}")
        if diag.get("missing_bw"):
            st.error(f"BW file missing columns: {diag['missing_bw']}")

        if diag.get("key_overlaps"):
            diag_rows = []
            for key, info in diag["key_overlaps"].items():
                diag_rows.append({
                    "Join Key (HYP)": key,
                    "HYP unique vals":  info["hyp_unique"],
                    "BW unique vals":   info["bw_unique"],
                    "Overlapping vals": info["overlap"],
                    "HYP samples": ", ".join(str(x) for x in info["sample_hyp"]),
                    "BW samples":  ", ".join(str(x) for x in info["sample_bw"]),
                })
            diag_df = pd.DataFrame(diag_rows)
            def _diag_style(row):
                if row["Overlapping vals"] == 0:
                    return ["background-color: #ffcccc"] * len(row)
                elif row["Overlapping vals"] < min(row["HYP unique vals"], row["BW unique vals"]):
                    return ["background-color: #ffffcc"] * len(row)
                return ["background-color: #ccffcc"] * len(row)
            st.dataframe(diag_df.style.apply(_diag_style, axis=1), use_container_width=True)
            zero_overlap = [r["Join Key (HYP)"] for r in diag_rows if r["Overlapping vals"] == 0]
            if zero_overlap:
                st.warning(f"⚠️ No overlapping values in: **{', '.join(zero_overlap)}** — rows on these keys will never match.")

        mc = diag.get("matched_rows", 0)
        ho = diag.get("hyp_only_rows", 0)
        bo = diag.get("bw_only_rows", 0)
        c1, c2, c3 = st.columns(3)
        c1.metric("Matched rows",      f"{mc:,}")
        c2.metric("HYP only (no BW)", f"{ho:,}")
        c3.metric("BW only (no HYP)", f"{bo:,}")

        st.divider()

        if result.empty:
            st.success("✅ No discrepancies found.")
        else:
            # ── Summary ──────────────────────────────────────────
            st.subheader("📋 Discrepancy Summary")
            hyp_only  = (result["Status"] == "HYP only — not in BW").sum()
            bw_only   = (result["Status"] == "BW only — not in HYP").sum()
            sign_diff = (result["Status"] == "Sign difference only").sum()
            val_diff  = (result["Status"] == "Value mismatch").sum()
            net       = result["Difference (HYP-BW)"].sum()

            c1, c2, c3, c4, c5 = st.columns(5)
            c1.metric("Total",           f"{len(result):,}")
            c2.metric("🟠 HYP only",     f"{hyp_only:,}")
            c3.metric("🔵 BW only",      f"{bw_only:,}")
            c4.metric("🟡 Sign diff",    f"{sign_diff:,}")
            c5.metric("🔴 Val mismatch", f"{val_diff:,}")
            st.metric("Net Difference (HYP − BW)", f"{net:,.2f}")

            # ── Filters ──────────────────────────────────────────
            st.subheader("🔎 Filter & Explore Discrepancies")

            # Row 1: Status / Scenario / Year / Period
            fc1, fc2, fc3, fc4 = st.columns(4)
            f_status   = fc1.multiselect("Status",   result["Status"].unique().tolist(),  default=result["Status"].unique().tolist(),  key="f_status")
            f_scenario = fc2.multiselect("Scenario", sorted(result["Scenario"].unique()), default=sorted(result["Scenario"].unique()), key="f_scenario")
            f_year     = fc3.multiselect("Year",     sorted(result["Year"].unique()),     default=sorted(result["Year"].unique()),     key="f_year")
            f_period   = fc4.multiselect("Period",
                sorted(result["Period"].unique(), key=lambda x: int(x) if str(x).isdigit() else 0),
                default=sorted(result["Period"].unique(), key=lambda x: int(x) if str(x).isdigit() else 0),
                key="f_period")

            # Row 2: Column-level filters
            fc5, fc6, fc7, fc8 = st.columns(4)
            f_entity  = fc5.multiselect("Entity",   sorted(result["Entity"].unique()),   default=sorted(result["Entity"].unique()),   key="f_entity")
            f_account = fc6.multiselect("Account",  sorted(result["Account"].unique()),  default=sorted(result["Account"].unique()),  key="f_account")
            f_icp     = fc7.multiselect("ICP",      sorted(result["ICP"].unique()),      default=sorted(result["ICP"].unique()),      key="f_icp")

            custom_cols = [c for c in ["Custom1","Custom2","Custom3","Custom4"] if c in result.columns]
            if custom_cols:
                f_custom_col = fc8.selectbox("Filter by Custom column", ["(none)"] + custom_cols, key="f_custom_col")
                if f_custom_col != "(none)":
                    f_custom_val = st.multiselect(
                        f"{f_custom_col} values",
                        sorted(result[f_custom_col].unique()),
                        default=sorted(result[f_custom_col].unique()),
                        key="f_custom_val"
                    )
                else:
                    f_custom_val = None
            else:
                f_custom_col = "(none)"
                f_custom_val = None

            # Apply all filters
            mask = (
                result["Status"].isin(f_status) &
                result["Scenario"].isin(f_scenario) &
                result["Year"].isin(f_year) &
                result["Period"].isin(f_period) &
                result["Entity"].isin(f_entity) &
                result["Account"].isin(f_account) &
                result["ICP"].isin(f_icp)
            )
            if f_custom_col != "(none)" and f_custom_val is not None:
                mask &= result[f_custom_col].isin(f_custom_val)

            filtered = result[mask].reset_index(drop=True)

            # Reset page to 1 when filter results change
            prev_len = st.session_state.get("filtered_len", -1)
            if len(filtered) != prev_len:
                st.session_state["page"] = 1
            st.session_state["filtered_len"] = len(filtered)

            st.caption(f"**{len(filtered):,}** rows match filters (of {len(result):,} total)")

            # ── Pagination ───────────────────────────────────────
            total_pages = max(1, (len(filtered) - 1) // PAGE_SIZE + 1)
            pcol1, pcol2, pcol3 = st.columns([1, 2, 1])
            if pcol1.button("◀ Prev", disabled=st.session_state["page"] <= 1):
                st.session_state["page"] -= 1
            if pcol3.button("Next ▶", disabled=st.session_state["page"] >= total_pages):
                st.session_state["page"] += 1
            pcol2.markdown(f"<div style='text-align:center;padding-top:8px'>Page <b>{st.session_state['page']}</b> / {total_pages}</div>", unsafe_allow_html=True)

            page  = st.session_state["page"]
            start = (page - 1) * PAGE_SIZE
            page_df = filtered.iloc[start : start + PAGE_SIZE]

            def colour_rows(row):
                return [f"background-color: {STATUS_COLORS.get(row['Status'], '')}"] * len(row)

            st.caption("💡 Bir satıra tıkla → o satırın tüm kaynak verisini aşağıda gör")
            sel = st.dataframe(
                page_df.style.apply(colour_rows, axis=1),
                use_container_width=True,
                height=520,
                on_select="rerun",
                selection_mode="single-row",
                key="main_table",
            )
            st.caption(f"Rows {start+1:,} – {min(start+PAGE_SIZE, len(filtered)):,} of {len(filtered):,}")

            # ── Drill-down ───────────────────────────────────────
            sel_rows = sel.selection.rows if sel.selection else []
            if sel_rows:
                row_idx  = sel_rows[0]
                sel_row  = page_df.iloc[row_idx]
                st.session_state["selected_row"] = sel_row.to_dict()

            if "selected_row" in st.session_state:
                sel_row  = st.session_state["selected_row"]
                hyp_norm = st.session_state["hyp_norm"]
                bw_norm  = st.session_state["bw_norm"]

                # Build key filter for HYP (uses HYP column names, already normalised)
                hyp_keys = [h for h, _ in JOIN_KEYS]
                bw_key_cols = [b for _, b in JOIN_KEYS]

                hyp_mask = pd.Series([True] * len(hyp_norm), index=hyp_norm.index)
                for col in hyp_keys:
                    if col in hyp_norm.columns and col in sel_row:
                        hyp_mask &= hyp_norm[col].astype(str).str.upper() == str(sel_row[col]).upper()

                # Build key filter for BW (rename cols back then filter)
                bw_mask = pd.Series([True] * len(bw_norm), index=bw_norm.index)
                for hyp_col, bw_col in JOIN_KEYS:
                    # bw_norm already has BW column names renamed to HYP names during compare
                    # so use hyp_col name in bw_norm
                    if hyp_col in bw_norm.columns and hyp_col in sel_row:
                        bw_mask &= bw_norm[hyp_col].astype(str).str.upper() == str(sel_row[hyp_col]).upper()

                hyp_detail = hyp_norm[hyp_mask].reset_index(drop=True)
                bw_detail  = bw_norm[bw_mask].reset_index(drop=True)

                # Restore BW original column names for display
                bw_col_restore = {h: b for h, b in JOIN_KEYS}
                bw_col_restore.update({f"{h}_BW": b for h, b in NUMERIC_MAP})
                bw_detail = bw_detail.rename(columns=bw_col_restore)

                st.divider()
                key_desc = " | ".join(f"{k}={sel_row.get(k,'?')}" for k in ["Scenario","Year","Period","Entity","Account"])
                st.subheader(f"🔍 Detay: {key_desc}")

                dcol1, dcol2 = st.columns(2)
                with dcol1:
                    st.markdown(f"**🟠 Hyperion (.dat) — {len(hyp_detail):,} satır**")
                    if hyp_detail.empty:
                        st.info("Bu anahtara ait HYP verisi yok.")
                    else:
                        st.dataframe(hyp_detail, use_container_width=True, height=300)
                        st.metric("HYP Amount Toplamı", f"{pd.to_numeric(hyp_detail['Amount'], errors='coerce').sum():,.4f}")

                with dcol2:
                    st.markdown(f"**🔵 BW (Excel) — {len(bw_detail):,} satır**")
                    if bw_detail.empty:
                        st.info("Bu anahtara ait BW verisi yok.")
                    else:
                        bw_show_cols = [b for _, b in JOIN_KEYS if b in bw_detail.columns] + \
                                       [b for _, b in NUMERIC_MAP if b in bw_detail.columns]
                        st.dataframe(bw_detail[bw_show_cols] if bw_show_cols else bw_detail,
                                     use_container_width=True, height=300)
                        st.metric("BW CUM_VALUE Toplamı", f"{pd.to_numeric(bw_detail.get('CUM_VALUE', pd.Series(dtype=float)), errors='coerce').sum():,.4f}")

            # ── Export ───────────────────────────────────────────
            st.divider()
            excel_bytes = to_excel_bytes(filtered)
            fname = f"HYP_BW_differences_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
            st.download_button(
                label="⬇️  Download filtered results as Excel",
                data=excel_bytes,
                file_name=fname,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                type="primary",
                use_container_width=True,
            )

else:
    st.info("👆 Upload both files above to start the comparison.")
