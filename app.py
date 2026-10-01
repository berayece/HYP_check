import streamlit as st
import pandas as pd
import numpy as np
import re
import io
from datetime import datetime
from pathlib import Path

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

# Parameter-entry accounts — differences on these are not reported as discrepancies
PARAM_ACCOUNTS_DEFAULT = ["N2172", "N2173"]

MONTH_MAP = {
    "jan":"1","feb":"2","mar":"3","apr":"4","may":"5","jun":"6",
    "jul":"7","aug":"8","sep":"9","oct":"10","nov":"11","dec":"12",
}
_NONE_RE = re.compile(r"^\[?(icp\s*)?none\]?$", re.IGNORECASE)

STATUS_COLORS = {          # pastel row tints for tables / Excel
    "HYP only — not in BW":  "#FBD5C4",
    "BW only — not in HYP":  "#C9DDF5",
    "Value mismatch":         "#F7C8C8",
}

# Report categories — order and hues validated for colour-blind separation (adjacent pairs)
IDENTICAL = "Eşleşiyor"
REPORT_CATS = [
    (IDENTICAL,              "Eşleşiyor",     "#1baf7a"),
    ("Value mismatch",       "Tutar farkı",   "#e34948"),
    ("BW only — not in HYP", "Sadece BW",     "#2a78d6"),
    ("HYP only — not in BW", "Sadece HYP",    "#eb6834"),
]
STATUS_LABEL = {k: lbl for k, lbl, _ in REPORT_CATS}
LABEL_ORDER  = [lbl for _, lbl, _ in REPORT_CATS]
LABEL_COLOR  = [c for _, _, c in REPORT_CATS]
MONTH_NAMES  = {str(i): m for i, m in enumerate(
    ["Oca", "Şub", "Mar", "Nis", "May", "Haz", "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara"], start=1)}

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

BW_EXPECTED = {"/BIC/ACCOUNTBI", "/BIC/COMPBI", "/BIC/SCNRBI", "CALYEAR", "CUM_VALUE"}

# Column order of the header-less BW CSV export (ZHYP_OH02), derived by matching rows against HYP
BW_CSV_COLUMNS = [
    "CALYEAR", "CUM_VALUE", "MONTH", "MTD_VALUE",
    "/BIC/ACCOUNTBI", "/BIC/COMPBI", "/BIC/CURBI", "/BIC/CURTYPE",
    "/BIC/CUSTOM1BI", "/BIC/CUSTOM2BI", "/BIC/CUSTOM3BI", "/BIC/CUSTOM4BI",
    "/BIC/ICPBI", "/BIC/ICPFLAG", "/BIC/SCNRBI",
]

def is_csv_name(name: str) -> bool:
    return Path(name).suffix.lower() in (".csv", ".txt")

def sniff_csv(file_bytes: bytes) -> tuple[str, str]:
    """Return (encoding, delimiter) for a BW CSV export."""
    head = file_bytes[:65536]
    if head.startswith((b"\xff\xfe", b"\xfe\xff")):
        enc = "utf-16"
    else:
        try:
            head[:-4].decode("utf-8-sig")
            enc = "utf-8-sig"
        except UnicodeDecodeError:
            enc = "cp1254"
    text = head.decode(enc, errors="ignore")
    lines = [l for l in text.splitlines()[:3] if l.strip()] or [""]
    sep = max([";", ",", "\t", "|"], key=lambda d: sum(l.count(d) for l in lines))
    return enc, sep

def _bw_header_row(rows: list[list[str]]) -> int:
    """Header is on row 1 or row 2 — pick the one with more expected BW columns."""
    hits = [len(BW_EXPECTED & {str(v).strip() for v in r}) for r in rows[:2]]
    return 1 if len(hits) > 1 and hits[1] > hits[0] else 0

@st.cache_data(show_spinner=False)
def load_bw(file_bytes: bytes, file_name: str) -> pd.DataFrame:
    if is_csv_name(file_name):
        enc, sep = sniff_csv(file_bytes)
        peek = file_bytes[:65536].decode(enc, errors="ignore").splitlines()[:2]
        rows = [l.split(sep) for l in peek]
        if not any(BW_EXPECTED & {v.strip() for v in r} for r in rows) \
                and rows and len(rows[0]) == len(BW_CSV_COLUMNS):
            # No header row — use the known column order of the BW CSV export
            df = pd.read_csv(io.BytesIO(file_bytes), encoding=enc, sep=sep, dtype=str,
                             header=None, names=BW_CSV_COLUMNS, on_bad_lines="warn")
        else:
            h = _bw_header_row(rows)
            df = pd.read_csv(io.BytesIO(file_bytes), encoding=enc, sep=sep, dtype=str,
                             skiprows=h, header=0, on_bad_lines="warn")
    else:
        raw = pd.read_excel(io.BytesIO(file_bytes), dtype=str, header=None)
        h = _bw_header_row(raw.head(2).fillna("").values.tolist())
        df = raw.iloc[h + 1:].reset_index(drop=True)
        df.columns = raw.iloc[h].fillna("").astype(str).tolist()
    df.columns = [str(c).strip() for c in df.columns]
    df = df.apply(lambda c: c.str.strip() if c.dtype == object else c)
    return df.dropna(how="all").reset_index(drop=True)

# ──────────────────────────────────────────────
# ZERO-ROW CLEANUP + SAVE CLEANED VERSION
# ──────────────────────────────────────────────

DEFAULT_SAVE_DIR = Path.home() / "Downloads"

def drop_zero_rows(df: pd.DataFrame, value_col: str) -> tuple[pd.DataFrame, int]:
    """Remove rows whose amount is zero or empty (compare() treats both as zero)."""
    if value_col not in df.columns:
        return df, 0
    vals = pd.to_numeric(df[value_col].astype(str).str.strip(), errors="coerce")
    keep = vals.abs() > 1e-4
    return df[keep].reset_index(drop=True), int((~keep).sum())

def _clean_path(folder: str, original_name: str, ext: str) -> Path:
    stem = Path(original_name).stem
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = Path(folder).expanduser()
    target.mkdir(parents=True, exist_ok=True)
    return target / f"{stem}_nozero_{ts}{ext}"

def save_clean_dat(df: pd.DataFrame, file_bytes: bytes, original_name: str, folder: str) -> Path:
    text = file_bytes.decode("utf-16")
    first_line = text.splitlines()[0] if text else "! DATA"
    eol = "\r\n" if "\r\n" in text[:5000] else "\n"
    body = df[DAT_COLUMNS].to_csv(sep=";", header=False, index=False, lineterminator=eol)
    path = _clean_path(folder, original_name, Path(original_name).suffix or ".dat")
    path.write_bytes((first_line + eol + body).encode("utf-16"))
    return path

def save_clean_bw(df: pd.DataFrame, file_bytes: bytes, original_name: str,
                  folder: str, fmt: str) -> Path:
    if fmt == "csv":
        enc, sep = sniff_csv(file_bytes) if is_csv_name(original_name) else ("utf-8-sig", ";")
        path = _clean_path(folder, original_name, ".csv")
        df.to_csv(path, sep=sep, index=False, encoding=enc)
        return path
    out = df.copy()
    for col in ("CUM_VALUE", "MTD_VALUE"):
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    path = _clean_path(folder, original_name, ".xlsx")
    out.to_excel(path, index=False, engine="openpyxl")
    return path

def pick_folder(initial: str):
    """Open the OS folder picker (app runs locally). Returns None if cancelled/unavailable."""
    import subprocess, sys
    try:
        if sys.platform == "darwin":
            init = str(Path(initial).expanduser()) if Path(initial).expanduser().is_dir() else str(Path.home())
            script = (f'POSIX path of (choose folder with prompt "Sıfırsız dosya nereye kaydedilsin?" '
                      f'default location POSIX file "{init}")')
            r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=600)
        elif sys.platform.startswith("win"):
            ps = ("Add-Type -AssemblyName System.Windows.Forms;"
                  "$d=New-Object System.Windows.Forms.FolderBrowserDialog;"
                  "$d.Description='Sifirsiz dosya nereye kaydedilsin?';"
                  f"$d.SelectedPath='{initial}';"
                  "$f=New-Object System.Windows.Forms.Form -Property @{TopMost=$true};"
                  "if($d.ShowDialog($f) -eq 'OK'){$d.SelectedPath}")
            r = subprocess.run(["powershell", "-NoProfile", "-STA", "-Command", ps],
                               capture_output=True, text=True, timeout=600)
        else:
            return None
        return r.stdout.strip() or None
    except Exception:
        return None

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
        return pd.DataFrame(), diag, hyp, bw

    hyp_c = normalise(hyp, hyp_keys)
    bw_c  = normalise(bw,  bw_keys)

    # Key overlap diagnostics
    for h_col, b_col in JOIN_KEYS:
        hyp_vals = set(hyp_c[h_col].dropna().unique())
        bw_vals  = set(bw_c[b_col].dropna().unique())
        overlap  = hyp_vals & bw_vals
        only_hyp = hyp_vals - bw_vals
        only_bw  = bw_vals  - hyp_vals
        diag["key_overlaps"][h_col] = {
            "hyp_unique": len(hyp_vals),
            "bw_unique":  len(bw_vals),
            "overlap":    len(overlap),
            "only_hyp":   sorted(only_hyp),
            "only_bw":    sorted(only_bw),
            "sample_hyp": sorted(hyp_vals)[:5],
            "sample_bw":  sorted(bw_vals)[:5],
        }

    bw_c = bw_c.rename(columns={b: h for h, b in JOIN_KEYS})
    bw_c = bw_c.rename(columns={b: f"{h}_BW" for h, b in NUMERIC_MAP})
    bw_full = bw_c.copy()  # keep all original BW columns (CURBI, CURTYPE, MTD_VALUE, ICPFLAG…) for drill-down
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
        # Compare absolute values — opposite signs (HYP −100 vs BW +100) are NOT a discrepancy
        diff     = hyp_vals.abs().fillna(0) - bw_vals.abs().fillna(0)

        # Exclude rows where both values are zero — only keep non-zero amounts
        # Tolerance: 1e-4 absolute, widened for huge amounts (~1e15) where float rounding alone exceeds it
        tol      = np.maximum(1e-4, 1e-12 * np.fmax(hyp_vals.abs(), bw_vals.abs()).fillna(0))
        nonzero  = (hyp_vals.abs() > 1e-4) | (bw_vals.abs() > 1e-4)
        both     = merged["_merge"] == "both"
        mismatch = nonzero & (~both | (diff.abs() > tol))
        diag["sign_flipped"] = int((nonzero & both & (diff.abs() <= tol)
                                    & (np.sign(hyp_vals) != np.sign(bw_vals))).sum())

        sub = merged.loc[mismatch, hyp_keys].copy()
        sub[f"HYP_Amount"]           = hyp_vals[mismatch].values
        sub[f"BW_CUM_VALUE"]         = bw_vals[mismatch].values
        sub["Difference (|HYP|-|BW|)"] = diff[mismatch].values
        sub["Abs Difference"]         = diff[mismatch].abs().values
        sub["Status"] = np.select(
            [merged.loc[mismatch, "_merge"].values == "left_only",
             merged.loc[mismatch, "_merge"].values == "right_only"],
            ["HYP only — not in BW", "BW only — not in HYP"], default="Value mismatch")
        results.append(sub)

    result = pd.concat(results, ignore_index=True) if results else pd.DataFrame()
    return result, diag, hyp_c, bw_full

# ──────────────────────────────────────────────
# EXCEL EXPORT
# ──────────────────────────────────────────────

def report_summary(result: pd.DataFrame, diag: dict) -> dict:
    counts = result["Status"].value_counts().to_dict() if not result.empty else {}
    matched = int(diag.get("matched_rows", 0))
    val_n   = int(counts.get("Value mismatch", 0))
    hyp_n   = int(counts.get("HYP only — not in BW", 0))
    bw_n    = int(counts.get("BW only — not in HYP", 0))
    ident   = max(matched - val_n, 0)   # includes opposite-sign rows (absolute values equal)
    total   = ident + val_n + hyp_n + bw_n

    def _abs(status, col="Abs Difference"):
        if result.empty:
            return 0.0
        return float(result.loc[result["Status"] == status, col].abs().sum())

    return {
        "total": total, "matched": matched, IDENTICAL: ident,
        "Value mismatch": val_n, "sign_flipped": int(diag.get("sign_flipped", 0)),
        "HYP only — not in BW": hyp_n, "BW only — not in HYP": bw_n,
        "val_abs":  _abs("Value mismatch"),
        "hyp_abs":  _abs("HYP only — not in BW", "HYP_Amount"),
        "bw_abs":   _abs("BW only — not in HYP", "BW_CUM_VALUE"),
    }

def breakdown(result: pd.DataFrame, dim: str) -> pd.DataFrame:
    """Difference counts per status + absolute difference, one row per dimension value."""
    if result.empty or dim not in result.columns:
        return pd.DataFrame()
    t = result.assign(_lbl=result["Status"].map(STATUS_LABEL))
    piv = t.pivot_table(index=dim, columns="_lbl", values="Abs Difference",
                        aggfunc="size", fill_value=0)
    piv = piv.reindex(columns=[l for l in LABEL_ORDER if l in piv.columns])
    piv["Toplam fark"] = piv.sum(axis=1)
    piv["Mutlak fark tutarı"] = t.groupby(dim)["Abs Difference"].sum()
    return piv.reset_index()

def _tr_int(n) -> str:
    return f"{int(n):,}".replace(",", ".")

def _period_label(periods) -> str:
    ps = sorted({str(p) for p in periods}, key=lambda v: int(v) if v.isdigit() else 99)
    if not ps:
        return "—"
    names = [MONTH_NAMES.get(p, p) for p in ps]
    nums = [int(p) for p in ps if p.isdigit()]
    if len(nums) == len(ps) and nums == list(range(nums[0], nums[-1] + 1)) and len(ps) > 2:
        return f"{names[0]} – {names[-1]} ({len(ps)} dönem)"
    return ", ".join(names)

def run_stats(hyp_norm: pd.DataFrame, bw_norm: pd.DataFrame) -> dict:
    """Scope + amount control figures of the compared data (after all filters)."""
    h = hyp_norm.assign(_amt=pd.to_numeric(hyp_norm.get("Amount"), errors="coerce"))
    b = bw_norm.assign(_amt=pd.to_numeric(bw_norm.get("Amount_BW"), errors="coerce"))

    def _vals(col):
        return sorted(set(h[col].dropna().astype(str)) | set(b[col].dropna().astype(str))) if col in h and col in b else []

    def _per(dim):
        g = pd.DataFrame({
            "HYP satır":   h.groupby(dim).size(),
            "BW satır":    b.groupby(dim).size(),
            "Σ|HYP|":      h.groupby(dim)["_amt"].apply(lambda x: x.abs().sum()),
            "Σ|BW|":       b.groupby(dim)["_amt"].apply(lambda x: x.abs().sum()),
        }).fillna(0)
        g["Fark (Σ|HYP| − Σ|BW|)"] = g["Σ|HYP|"] - g["Σ|BW|"]
        g[["HYP satır", "BW satır"]] = g[["HYP satır", "BW satır"]].astype(int)
        return g.reset_index().rename(columns={"index": dim})

    per_period = _per("Period").sort_values("Period", key=lambda c: pd.to_numeric(c, errors="coerce"))
    per_period.insert(1, "Ay", per_period["Period"].map(lambda v: MONTH_NAMES.get(str(v), str(v))))
    return {
        "scenarios": _vals("Scenario"), "years": _vals("Year"), "periods": _vals("Period"),
        "n_entity": len(_vals("Entity")), "n_account": len(_vals("Account")), "n_icp": len(_vals("ICP")),
        "hyp_rows": len(h), "bw_rows": len(b),
        "hyp_abs": float(h["_amt"].abs().sum()), "bw_abs": float(b["_amt"].abs().sum()),
        "hyp_net": float(h["_amt"].sum()), "bw_net": float(b["_amt"].sum()),
        "per_period": per_period,
        "per_entity": _per("Entity").sort_values("Σ|HYP|", ascending=False),
    }

def _write_report_sheet(wb, full: pd.DataFrame, diag: dict, stats: dict, meta: dict):
    """One-page reconciliation report — readable even (especially) when there are no differences."""
    from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
    from openpyxl.chart import BarChart, Reference
    from openpyxl.chart.label import DataLabelList

    sm = report_summary(full, diag)
    ws = wb.create_sheet("Rapor", 0)
    ws.sheet_view.showGridLines = False
    for col in "ABCDEFGH":
        ws.column_dimensions[col].width = 17
    ws.column_dimensions["A"].width = 30

    fill = lambda c: PatternFill(fill_type="solid", fgColor=c.lstrip("#"))
    thin = Side(style="thin", color="D9D8D3")
    box = Border(left=thin, right=thin, top=thin, bottom=thin)
    muted = Font(color="6B6A65", size=10)
    h2 = Font(bold=True, size=13, color="1F1F1D")

    def section(row, title):
        ws.cell(row, 1, title).font = h2
        for c in range(1, 9):
            ws.cell(row, c).border = Border(bottom=Side(style="medium", color="1F1F1D"))

    def kv(row, col, label, value, fmt=None):
        ws.cell(row, col, label).font = muted
        v = ws.cell(row, col + 1, value)
        v.font = Font(bold=True)
        v.alignment = Alignment(horizontal="left")
        if fmt:
            v.number_format = fmt

    # Title
    ws["A1"] = "HYP – BW Mutabakat Raporu"
    ws["A1"].font = Font(bold=True, size=20)
    run_at = meta.get("run_at") or datetime.now()
    ws["A2"] = f"Oluşturulma: {run_at:%d.%m.%Y %H:%M}"
    ws["A2"].font = muted

    # Banner
    diffs = sm["total"] - sm[IDENTICAL]
    ws.merge_cells("A4:H4")
    ws.row_dimensions[4].height = 34
    if diffs == 0:
        ws["A4"] = "✅  FARK BULUNMADI — HYP ve BW verileri mutabık (mutlak değerle karşılaştırma)"
        ws["A4"].fill, ws["A4"].font = fill("E3F4EC"), Font(bold=True, size=13, color="0E6B47")
    else:
        ws["A4"] = f"⚠️  {_tr_int(diffs)} FARK SATIRI BULUNDU — ayrıntı 'Farklar' sayfasında"
        ws["A4"].fill, ws["A4"].font = fill("FBE3E3"), Font(bold=True, size=13, color="9B1C1C")
    ws["A4"].alignment = Alignment(vertical="center", indent=1)

    # Scope
    section(6, "Kapsam")
    kv(7, 1, "Senaryo", ", ".join(stats["scenarios"]) or "—")
    kv(7, 5, "Yıl", ", ".join(stats["years"]) or "—")
    kv(8, 1, "Dönem", _period_label(stats["periods"]))
    kv(8, 5, "Entity sayısı", stats["n_entity"], "#,##0")
    kv(9, 1, "Hesap sayısı", stats["n_account"], "#,##0")
    kv(9, 5, "ICP değer sayısı", stats["n_icp"], "#,##0")
    kv(10, 1, "HYP dosyası", meta.get("hyp_file", "—"))
    kv(11, 1, "BW dosyası", meta.get("bw_file", "—"))

    # KPI tiles (mirrors the on-screen report)
    section(13, "Sonuç")
    tiles = [(lbl, sm[k], c) for k, lbl, c in REPORT_CATS]
    for i, (lbl, n, colour) in enumerate(tiles):
        c0 = 1 + i * 2
        for r in (14, 15, 16):
            ws.merge_cells(start_row=r, start_column=c0, end_row=r, end_column=c0 + 1)
        head = ws.cell(14, c0, lbl)
        head.fill, head.font = fill(colour), Font(bold=True, color="FFFFFF", size=11)
        head.alignment = Alignment(horizontal="center")
        val = ws.cell(15, c0, n)
        val.font, val.number_format = Font(bold=True, size=22), "#,##0"
        val.alignment = Alignment(horizontal="center")
        pct = ws.cell(16, c0, n / sm["total"] if sm["total"] else 0)
        pct.font, pct.number_format = muted, "0.0%"
        pct.alignment = Alignment(horizontal="center")
        for r in (14, 15, 16):
            for c in (c0, c0 + 1):
                ws.cell(r, c).border = box
    ws.row_dimensions[15].height = 34

    # Composition chart (data in hidden helper columns)
    ws["K14"] = "Durum"
    for i, (lbl, n, _) in enumerate(tiles):
        ws.cell(14, 12 + i, lbl)
        ws.cell(15, 12 + i, n)
    ws["K15"] = "Satır"
    for col in "KLMNO":
        ws.column_dimensions[col].hidden = True
    ch = BarChart()
    ch.type, ch.grouping, ch.overlap = "bar", "percentStacked", 100
    ch.add_data(Reference(ws, min_col=12, max_col=11 + len(tiles), min_row=14, max_row=15), titles_from_data=True)
    ch.set_categories(Reference(ws, min_col=11, min_row=15))
    for ser, (_, _, colour) in zip(ch.series, tiles):
        ser.graphicalProperties.solidFill = colour.lstrip("#")
        ser.graphicalProperties.line.solidFill = "FFFFFF"
    ch.legend.position = "t"
    ch.y_axis.number_format = "0%"
    ch.y_axis.majorGridlines = None
    ch.x_axis.delete = True
    ch.height, ch.width = 3.6, 24
    ws.add_chart(ch, "A18")

    # Data preparation
    section(26, "Veri hazırlığı")
    for c, t in enumerate(["Adım", "HYP", "BW"], start=1):
        x = ws.cell(27, c, t)
        x.font, x.fill = Font(bold=True), fill("E9E8E4")
    prep = [
        ("Dosyadan okunan satır", meta.get("hyp_loaded"), meta.get("bw_loaded")),
        ("Sıfır / boş tutar silinen", meta.get("hyp_zero"), meta.get("bw_zero")),
        ("CURTYPE filtresi dışı", None, meta.get("curtype_excluded")),
        ("Custom1/2/4 filtresi dışı", meta.get("custom_excl_hyp"), meta.get("custom_excl_bw")),
        ("Karşılaştırılan satır", stats["hyp_rows"], stats["bw_rows"]),
    ]
    for i, (lbl, hv, bv) in enumerate(prep, start=28):
        ws.cell(i, 1, lbl)
        for c, v in ((2, hv), (3, bv)):
            cell = ws.cell(i, c, v if v is not None else "—")
            cell.number_format = "#,##0"
            cell.alignment = Alignment(horizontal="right")
    for c in (1, 2, 3):
        ws.cell(32, c).font = Font(bold=True)

    # Amount control
    section(34, "Tutar kontrolü")
    amt_cols = [(2, "HYP"), (4, "BW"), (6, "Fark (HYP − BW)")]   # each value spans two columns
    ws.cell(35, 1).fill = fill("E9E8E4")
    for c, t in amt_cols:
        ws.merge_cells(start_row=35, start_column=c, end_row=35, end_column=c + 1)
        x = ws.cell(35, c, t)
        x.font, x.fill, x.alignment = Font(bold=True), fill("E9E8E4"), Alignment(horizontal="right")
    for r, (lbl, hv, bv) in enumerate([("Mutlak toplam  Σ|tutar|", stats["hyp_abs"], stats["bw_abs"]),
                                        ("Net toplam  Σ tutar", stats["hyp_net"], stats["bw_net"])], start=36):
        ws.cell(r, 1, lbl)
        for (c, _), v in zip(amt_cols, (hv, bv, hv - bv)):
            ws.merge_cells(start_row=r, start_column=c, end_row=r, end_column=c + 1)
            ws.cell(r, c, v).number_format = "#,##0.00"
    expl = "Net toplamlar arasındaki fark ters işaretli satırlardan kaynaklanır; mutabakat mutlak değer üzerinden yapılır."
    if diag.get("param_excluded"):
        expl += " Mutlak toplam farkı, sorun sayılmayan parametre hesaplarından gelebilir."
    ws.cell(38, 1, expl).font = muted

    # Notes
    section(40, "Notlar")
    notes = [
        "Karşılaştırma mutlak değerle yapılır; tutarı aynı, işareti ters satırlar eşleşiyor sayılır "
        f"(bu çalıştırmada {_tr_int(sm['sign_flipped'])} satır).",
        "Eşleştirme anahtarı: Senaryo, Yıl, Dönem, Entity, Hesap, ICP, Custom1–4.",
        "Tolerans: 0,0001 (çok büyük tutarlarda göreli 10⁻¹²).",
    ]
    if meta.get("curtype_selected") is not None:
        notes.append(f"BW CURTYPE dahil edilen değerler: {', '.join(meta['curtype_selected']) or '—'}.")
    if diag.get("param_accounts"):
        notes.append(f"Parametre hesapları ({', '.join(diag['param_accounts'])}): "
                     f"{_tr_int(diag.get('param_excluded', 0))} fark satırı sorun sayılmadı.")
    for i, n in enumerate(notes, start=41):
        ws.cell(i, 1, f"•  {n}")

    ws.print_area = "A1:H" + str(41 + len(notes))
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.sheet_properties.pageSetUpPr.fitToPage = True

def to_excel_bytes(df: pd.DataFrame, diag=None, full=None, stats=None, meta=None) -> bytes:
    """Detail sheet = df (filtered); summary/breakdown sheets = full result."""
    full = df if full is None else full
    from openpyxl.styles import PatternFill, Font, Alignment
    from openpyxl.formatting.rule import FormulaRule
    from openpyxl.utils import get_column_letter
    buf = io.BytesIO()
    bold = Font(bold=True)
    head_fill = PatternFill(fill_type="solid", fgColor="E9E8E4")

    def _fmt(ws, data: pd.DataFrame, num_cols=()):
        for i, col in enumerate(data.columns, start=1):
            sample = data[col].head(1000).astype(str)
            w = max([len(str(col))] + sample.str.len().tolist()) + 2
            ws.column_dimensions[get_column_letter(i)].width = min(max(w, 10), 45)
            if col in num_cols:
                for (cell,) in ws.iter_rows(min_row=2, min_col=i, max_col=i):
                    cell.number_format = "#,##0.00"
        for cell in ws[1]:
            cell.font, cell.fill = bold, head_fill
        ws.freeze_panes = "A2"
        if ws.max_row > 1:
            ws.auto_filter.ref = ws.dimensions

    num_cols = {"HYP_Amount", "BW_CUM_VALUE", "Difference (|HYP|-|BW|)", "Abs Difference", "Mutlak fark tutarı"}
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        # Report sheet + scope sheets
        if diag is not None:
            if stats is not None:
                _write_report_sheet(writer.book, full, diag, stats, meta or {})
                for key, sheet in (("per_period", "Dönem özeti"), ("per_entity", "Entity özeti")):
                    stats[key].to_excel(writer, sheet_name=sheet, index=False)
                    _fmt(writer.sheets[sheet], stats[key], {"Σ|HYP|", "Σ|BW|", "Fark (Σ|HYP| − Σ|BW|)"})

            for dim, sheet in (("Entity", "Fark-Entity"), ("Account", "Fark-Hesap"),
                               ("Period", "Fark-Dönem"), ("Custom4", "Fark-Custom4")):
                b = breakdown(full, dim)
                if b.empty:
                    continue
                if dim == "Period":
                    b = b.sort_values(dim, key=lambda c: pd.to_numeric(c, errors="coerce"))
                else:
                    b = b.sort_values("Toplam fark", ascending=False)
                b.to_excel(writer, sheet_name=sheet, index=False)
                _fmt(writer.sheets[sheet], b, num_cols)

        # Detail sheet — status colour via conditional formatting (fast on large files)
        # (no differences → no empty detail sheet; the report sheet says so)
        if not (df.empty and diag is not None and stats is not None):
            df.to_excel(writer, sheet_name="Farklar", index=False)
            ws = writer.sheets["Farklar"]
            _fmt(ws, df, num_cols)
            if "Status" in df.columns and len(df):
                col = get_column_letter(list(df.columns).index("Status") + 1)
                rng = f"A2:{get_column_letter(len(df.columns))}{len(df) + 1}"
                for status, colour in STATUS_COLORS.items():
                    ws.conditional_formatting.add(rng, FormulaRule(
                        formula=[f'${col}2="{status}"'],
                        fill=PatternFill(fill_type="solid", fgColor=colour.lstrip("#"), bgColor=colour.lstrip("#"))))
    return buf.getvalue()

def _stacked_bar(data: pd.DataFrame, dim: str, horizontal: bool = True, sort=None):
    import altair as alt
    long = data.melt(id_vars=[dim], value_vars=[l for l in LABEL_ORDER if l in data.columns],
                     var_name="Durum", value_name="Satır")
    long = long[long["Satır"] > 0]
    colour = alt.Color("Durum:N", scale=alt.Scale(domain=LABEL_ORDER, range=LABEL_COLOR),
                       legend=alt.Legend(orient="top", title=None))
    order = alt.Order("_o:Q")
    long["_o"] = long["Durum"].map({l: i for i, l in enumerate(LABEL_ORDER)})
    tip = [alt.Tooltip(f"{dim}:N"), alt.Tooltip("Durum:N"), alt.Tooltip("Satır:Q", format=",")]
    if horizontal:
        enc = dict(y=alt.Y(f"{dim}:N", sort=sort, title=None),
                   x=alt.X("Satır:Q", title="Fark satırı", axis=alt.Axis(format="~s")))
    else:
        enc = dict(x=alt.X(f"{dim}:N", sort=sort, title=None, axis=alt.Axis(labelAngle=0)),
                   y=alt.Y("Satır:Q", title="Fark satırı", axis=alt.Axis(format="~s")))
    return (alt.Chart(long).mark_bar(cornerRadiusEnd=3, stroke="white", strokeWidth=1)
            .encode(color=colour, order=order, tooltip=tip, **enc))

def render_report(result: pd.DataFrame, diag: dict, stats=None):
    import altair as alt
    sm = report_summary(result, diag)
    total = sm["total"] or 1
    pct_of = lambda n, d: f"%{n / d * 100:.1f}".replace(".", ",")
    pct = lambda n: pct_of(n, total)
    fmt = lambda n: f"{n:,.0f}".replace(",", ".")
    money = lambda n: f"{n:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    st.header("📊 Karşılaştırma raporu")
    if stats:
        st.markdown(
            f"**Senaryo** {', '.join(stats['scenarios']) or '—'} · **Yıl** {', '.join(stats['years']) or '—'} · "
            f"**Dönem** {_period_label(stats['periods'])} · **{fmt(stats['n_entity'])}** entity · "
            f"**{fmt(stats['n_account'])}** hesap · HYP **{fmt(stats['hyp_rows'])}** / BW **{fmt(stats['bw_rows'])}** satır")

    # ── Headline ───────────────────────────────
    diffs = total - sm[IDENTICAL]
    biggest = max(REPORT_CATS[1:], key=lambda c: sm[c[0]])
    lines = [f"Karşılaştırılan **{fmt(sm['total'])}** satırın **{pct(sm[IDENTICAL])}** kadarı iki sistemde eşleşiyor (mutlak değerle)."]
    if diffs:
        if sm[biggest[0]] == diffs:
            lines.append(f"Farklı çıkan **{fmt(diffs)}** satırın tamamı **{biggest[1]}**.")
        else:
            lines.append(f"Farklı çıkan **{fmt(diffs)}** satırın en büyük grubu **{biggest[1]}**: "
                         f"{fmt(sm[biggest[0]])} satır, tüm farkların {pct_of(sm[biggest[0]], diffs)} kadarı.")
    if sm["Value mismatch"]:
        lines.append(f"Gerçek tutar farkı olan satır sayısı **{fmt(sm['Value mismatch'])}**, "
                     f"mutlak fark toplamı **{money(sm['val_abs'])}**.")
    st.info(" ".join(lines))

    # ── KPI tiles ──────────────────────────────
    k = st.columns(4)
    flip = sm["sign_flipped"]
    tiles = [
        ("✅ Eşleşiyor", sm[IDENTICAL], pct(sm[IDENTICAL]),
         "Anahtar iki dosyada da var ve mutlak tutarlar aynı. "
         f"Bunların {fmt(flip)} tanesi ters işaretli (örn. HYP −100, BW +100) — sorun sayılmaz."),
        ("🔴 Tutar farkı", sm["Value mismatch"], f"Σ|fark| {money(sm['val_abs'])}",
         "Anahtar iki tarafta da var ama mutlak tutarlar farklı"),
        ("🔵 Sadece BW", sm["BW only — not in HYP"], pct(sm["BW only — not in HYP"]),
         "BW'de olup HYP'de olmayan satırlar"),
        ("🟠 Sadece HYP", sm["HYP only — not in BW"], pct(sm["HYP only — not in BW"]),
         "HYP'de olup BW'de olmayan satırlar"),
    ]
    for col, (label, n, sub, tip) in zip(k, tiles):
        with col.container(border=True):
            st.metric(label, fmt(n), help=tip)
            st.caption(sub)
    if diag.get("param_excluded"):
        st.caption(f"ℹ️ Parametre hesaplarındaki ({', '.join(diag['param_accounts'])}) "
                   f"{fmt(diag['param_excluded'])} fark satırı sorun sayılmadı ve rapora dahil edilmedi.")
    if flip:
        st.caption(f"ℹ️ Karşılaştırma mutlak değerle yapılır. {fmt(flip)} satırda işaret ters ama tutar aynı; "
                   "bunlar eşleşiyor sayıldı.")

    # ── Composition bar ────────────────────────
    comp = pd.DataFrame({"Durum": LABEL_ORDER, "Satır": [sm[c] for c, _, _ in REPORT_CATS], "y": "Tümü"})
    comp["Oran"] = comp["Satır"] / total
    comp["_o"] = range(len(comp))
    comp = comp[comp["Satır"] > 0]
    bar = (alt.Chart(comp).mark_bar(height=34, stroke="white", strokeWidth=2)
           .encode(x=alt.X("Satır:Q", stack="normalize", axis=alt.Axis(format="%", title=None)),
                   y=alt.Y("y:N", axis=None),
                   color=alt.Color("Durum:N", scale=alt.Scale(domain=LABEL_ORDER, range=LABEL_COLOR),
                                   legend=alt.Legend(orient="top", title=None)),
                   order=alt.Order("_o:Q"),
                   tooltip=["Durum", alt.Tooltip("Satır:Q", format=","), alt.Tooltip("Oran:Q", format=".1%")])
           .properties(height=80))
    st.altair_chart(bar, use_container_width=True)

    # ── Where are the differences? ─────────────
    if result.empty:
        st.success("✅ Hiç fark yok.")
        return
    st.subheader("📍 Farklar nerede yoğunlaşıyor?")
    st.caption("Sadece farklı çıkan satırlar sayılır. Çubuğun üzerine gel → sayı.")
    tabs = st.tabs(["Entity", "Hesap", "Dönem", "Custom4"])
    for tab, dim in zip(tabs, ["Entity", "Account", "Period", "Custom4"]):
        with tab:
            b = breakdown(result, dim)
            if b.empty:
                st.info("Veri yok.")
                continue
            if dim == "Period":
                b = b.sort_values(dim, key=lambda c: pd.to_numeric(c, errors="coerce"))
                b[dim] = b[dim].map(lambda v: MONTH_NAMES.get(str(v), str(v)))
                chart = _stacked_bar(b, dim, horizontal=False, sort=list(b[dim])).properties(height=320)
            else:
                b = b.sort_values("Toplam fark", ascending=False)
                top = b.head(15)
                chart = _stacked_bar(top, dim, sort=list(top[dim])).properties(height=max(160, 26 * len(top)))
                if len(b) > 15:
                    st.caption(f"En çok farkı olan 15 {dim} gösteriliyor (toplam {len(b)}). Tamamı aşağıdaki tabloda.")
            st.altair_chart(chart, use_container_width=True)
            st.dataframe(b, use_container_width=True, hide_index=True, height=240,
                         column_config={"Mutlak fark tutarı": st.column_config.NumberColumn(format="%,.2f")})

    # ── Biggest real differences ───────────────
    st.subheader("💰 En büyük 20 tutar farkı")
    st.caption("Tutar farkı, sadece HYP ve sadece BW satırları; mutlak değerler arasındaki farka göre sıralı.")
    big = result.nlargest(20, "Abs Difference").copy()
    big.insert(0, "Durum", big.pop("Status").map(STATUS_LABEL))
    show = ["Durum", "Entity", "Account", "Period", "ICP", "Custom1", "Custom4",
            "HYP_Amount", "BW_CUM_VALUE", "Difference (|HYP|-|BW|)"]
    money = st.column_config.NumberColumn(format="%,.2f")
    st.dataframe(big[[c for c in show if c in big.columns]], use_container_width=True, hide_index=True,
                 column_config={"HYP_Amount": money, "BW_CUM_VALUE": money, "Difference (|HYP|-|BW|)": money})


# ──────────────────────────────────────────────
# UI
# ──────────────────────────────────────────────

st.title("🔍 HYP vs BW Comparison Tool")

def _on_pick(dir_key: str):
    chosen = pick_folder(st.session_state.get(dir_key) or str(DEFAULT_SAVE_DIR))
    if chosen:
        st.session_state[dir_key] = chosen

def save_prompt(kind: str, f, df: pd.DataFrame, removed: int):
    """Ask where to save the zero-free version right after a file is read."""
    states = st.session_state.setdefault("clean_saved", {})
    file_key = f"{kind}|{f.name}|{f.size}"
    state = states.get(file_key)
    if not removed:
        return
    if state and state.startswith("saved:"):
        st.caption(f"💾 Sıfırsız versiyon kaydedildi: `{state[6:]}`")
        return
    if state == "skipped":
        st.caption("Sıfırsız versiyon kaydedilmedi.")
        return

    with st.container(border=True):
        st.markdown(f"**{removed:,} sıfır satır silindi.** Sıfırsız versiyonu nereye kaydedeyim?")
        dir_key = f"save_dir_{kind}"
        if dir_key not in st.session_state:
            st.session_state[dir_key] = st.session_state.get("last_save_dir", str(DEFAULT_SAVE_DIR))
        st.text_input("Klasör", key=dir_key)
        fmt = None
        if kind == "BW":
            fmt_opts = ["xlsx", "csv"]
            fmt = st.radio("Format", fmt_opts, horizontal=True, key=f"save_fmt_{kind}",
                           index=1 if is_csv_name(f.name) else 0,
                           help="Büyük dosyalarda CSV çok daha hızlı yazılır.")
        b1, b2, b3 = st.columns(3)
        b1.button("📁 Klasör seç…", key=f"pick_{kind}", on_click=_on_pick, args=(dir_key,),
                  use_container_width=True)
        do_save = b2.button("💾 Kaydet", key=f"save_{kind}", type="primary", use_container_width=True)
        if b3.button("Kaydetme", key=f"skip_{kind}", use_container_width=True):
            states[file_key] = "skipped"
            st.rerun()
        if do_save:
            folder = st.session_state[dir_key].strip() or str(DEFAULT_SAVE_DIR)
            with st.spinner(f"Sıfırsız {kind} versiyonu kaydediliyor…"):
                try:
                    path = save_clean_dat(df, f.getvalue(), f.name, folder) if kind == "HYP" \
                           else save_clean_bw(df, f.getvalue(), f.name, folder, fmt)
                except Exception as e:
                    st.error(f"Kaydedilemedi: {e}")
                    return
            states[file_key] = f"saved:{path}"
            st.session_state["last_save_dir"] = folder
            st.rerun()

hyp_df = bw_df = None
col1, col2 = st.columns(2)

with col1:
    st.subheader("📂 Hyperion .dat file")
    dat_file = st.file_uploader("Upload .dat file", type=["dat", "txt", "csv"], key="dat")
    if dat_file:
        with st.spinner("HYP okunuyor…"):
            hyp_df, hyp_zero = drop_zero_rows(load_dat(dat_file.getvalue()), "Amount")
        st.success(f"✅ HYP loaded — {len(hyp_df):,} rows ({hyp_zero:,} sıfır satır silindi)")
        save_prompt("HYP", dat_file, hyp_df, hyp_zero)
        with st.expander("Preview HYP data"):
            st.dataframe(hyp_df.head(10), use_container_width=True)

with col2:
    st.subheader("📊 BW backup file (Excel / CSV)")
    bw_file = st.file_uploader("Upload BW Excel or CSV file", type=["xlsx", "xls", "csv"], key="bw")
    if bw_file:
        with st.spinner("BW okunuyor…"):
            bw_df, bw_zero = drop_zero_rows(load_bw(bw_file.getvalue(), bw_file.name), "CUM_VALUE")
        st.success(f"✅ BW loaded — {len(bw_df):,} rows ({bw_zero:,} sıfır satır silindi)")
        save_prompt("BW", bw_file, bw_df, bw_zero)
        with st.expander("Preview BW data"):
            st.dataframe(bw_df.head(10), use_container_width=True)

if hyp_df is not None and bw_df is not None:
    bw_df_filtered = bw_df
    curtype_col = "/BIC/CURTYPE"
    if curtype_col in bw_df.columns:
        curtype_vals = sorted(bw_df[curtype_col].dropna().astype(str).str.strip().unique().tolist())
        default_vals = [v for v in curtype_vals if v != "?"]
        st.subheader("🧮 BW CURTYPE filtresi")
        selected_curtypes = st.multiselect(
            "Analize dahil edilecek /BIC/CURTYPE değerleri ('?' varsayılan olarak dışarıda bırakılır)",
            options=curtype_vals,
            default=default_vals,
        )
        bw_df_filtered = bw_df[bw_df[curtype_col].astype(str).str.strip().isin(selected_curtypes)].reset_index(drop=True)
        excluded = len(bw_df) - len(bw_df_filtered)
        if excluded:
            st.caption(f"⛔ {excluded:,} BW satırı ({', '.join(sorted(set(curtype_vals) - set(selected_curtypes))) or '—'}) analiz dışında bırakıldı.")

    hyp_df_filtered = hyp_df
    custom_filter_cols = [
        ("/BIC/CUSTOM1BI", "Custom1"),
        ("/BIC/CUSTOM2BI", "Custom2"),
        ("/BIC/CUSTOM4BI", "Custom4"),
    ]
    present_custom_cols = [(c, l) for c, l in custom_filter_cols
                            if c in bw_df_filtered.columns and l in hyp_df_filtered.columns]
    if present_custom_cols:
        st.subheader("🧮 Custom1 / Custom2 / Custom4 filtresi (BW + HYP)")
        st.caption("Bu sütunlar eşleştirme anahtarı olduğu için seçim hem BW hem HYP verisine birlikte uygulanır.")
        before_custom_bw  = len(bw_df_filtered)
        before_custom_hyp = len(hyp_df_filtered)
        ui_cols = st.columns(len(present_custom_cols))
        for (col_name, label), ui_col in zip(present_custom_cols, ui_cols):
            with ui_col:
                bw_vals  = bw_df_filtered[col_name].dropna().astype(str).str.strip().unique().tolist()
                hyp_vals = hyp_df_filtered[label].dropna().astype(str).str.strip().unique().tolist()
                raw_vals = sorted(set(bw_vals) | set(hyp_vals))
                default_vals = [v for v in raw_vals if _NONE_RE.match(v)]
                selected_vals = st.multiselect(
                    f"{label} — sadece NONE varsayılan",
                    options=raw_vals,
                    default=default_vals,
                    key=f"filt_{col_name}",
                )
                bw_df_filtered  = bw_df_filtered[bw_df_filtered[col_name].astype(str).str.strip().isin(selected_vals)].reset_index(drop=True)
                hyp_df_filtered = hyp_df_filtered[hyp_df_filtered[label].astype(str).str.strip().isin(selected_vals)].reset_index(drop=True)
        excluded_custom_bw  = before_custom_bw  - len(bw_df_filtered)
        excluded_custom_hyp = before_custom_hyp - len(hyp_df_filtered)
        if excluded_custom_bw or excluded_custom_hyp:
            st.caption(f"⛔ Filtre sonrası dışarıda kalan satırlar — BW: {excluded_custom_bw:,}, HYP: {excluded_custom_hyp:,}")

    acc_opts = sorted(set(hyp_df_filtered.get("Account", pd.Series(dtype=str)).dropna().astype(str).str.strip().str.upper())
                      | set(bw_df_filtered.get("/BIC/ACCOUNTBI", pd.Series(dtype=str)).dropna().astype(str).str.strip().str.upper())
                      | set(PARAM_ACCOUNTS_DEFAULT))
    st.subheader("🧮 Parametre hesapları")
    param_accounts = st.multiselect(
        "Bu hesaplardaki farklar sorun olarak sayılmaz (parametre girişi)",
        options=acc_opts, default=PARAM_ACCOUNTS_DEFAULT, key="param_accounts",
    )

    st.divider()
    if st.button("▶️ Run Comparison", type="primary", use_container_width=True):
        with st.spinner("Comparing…"):
            result, diag, hyp_norm, bw_norm = compare(hyp_df_filtered, bw_df_filtered)
            if not result.empty and param_accounts:
                is_param = result["Account"].isin([a.upper() for a in param_accounts])
                diag["param_excluded"] = int(is_param.sum())
                diag["param_accounts"] = list(param_accounts)
                result = result[~is_param].reset_index(drop=True)
            g = globals()
            meta = {
                "run_at": datetime.now(), "hyp_file": dat_file.name, "bw_file": bw_file.name,
                "hyp_loaded": len(hyp_df) + hyp_zero, "hyp_zero": hyp_zero,
                "bw_loaded": len(bw_df) + bw_zero, "bw_zero": bw_zero,
                "curtype_excluded": g.get("excluded"), "curtype_selected": g.get("selected_curtypes"),
                "custom_excl_hyp": g.get("excluded_custom_hyp"), "custom_excl_bw": g.get("excluded_custom_bw"),
            }
            stats = run_stats(hyp_norm, bw_norm)
        st.session_state["run_meta"]  = meta
        st.session_state["run_stats"] = stats
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

        render_report(result, diag, st.session_state.get("run_stats"))

        with st.expander("🔬 Teknik detay — anahtar sütun eşleşmeleri"):
            # ── Diagnostics ──────────────────────────────────────────
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
                        "Only HYP":        len(info.get("only_hyp", [])),
                        "Only BW":         len(info.get("only_bw",  [])),
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

                # ── Unique value breakdown per key ───────────────────
                if st.toggle("📋 Tekil değer detayı — yalnızca HYP / yalnızca BW", key="t_unique"):
                    for key, info in diag["key_overlaps"].items():
                        only_hyp_list = info.get("only_hyp", [])
                        only_bw_list  = info.get("only_bw",  [])
                        if not only_hyp_list and not only_bw_list:
                            continue
                        st.markdown(f"#### {key}")
                        uc1, uc2 = st.columns(2)
                        with uc1:
                            st.markdown(f"**🟠 Yalnızca HYP — {len(only_hyp_list):,} değer**")
                            if only_hyp_list:
                                st.dataframe(
                                    pd.DataFrame({"HYP only": only_hyp_list}),
                                    use_container_width=True, height=220,
                                )
                            else:
                                st.info("Fark yok")
                        with uc2:
                            st.markdown(f"**🔵 Yalnızca BW — {len(only_bw_list):,} değer**")
                            if only_bw_list:
                                st.dataframe(
                                    pd.DataFrame({"BW only": only_bw_list}),
                                    use_container_width=True, height=220,
                                )
                            else:
                                st.info("Fark yok")


        st.divider()

        if not result.empty:
            st.subheader("🔎 Detay tablosu")

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
                        st.dataframe(bw_detail, use_container_width=True, height=300)
                        st.metric("BW CUM_VALUE Toplamı", f"{pd.to_numeric(bw_detail.get('CUM_VALUE', pd.Series(dtype=float)), errors='coerce').sum():,.4f}")

        # ── Export (always available — also when there are no differences) ──
        st.divider()
        filtered_x = filtered if not result.empty else result
        sig = (len(result), len(filtered_x), float(filtered_x["Abs Difference"].sum()) if len(filtered_x) else 0.0,
               id(st.session_state.get("run_stats")))
        if st.session_state.get("excel_sig") != sig:
            st.session_state.pop("excel_bytes", None)
        if "excel_bytes" not in st.session_state:
            if st.button("📄 Excel mutabakat raporunu hazırla", use_container_width=True,
                         help="Rapor sayfası (kapsam, sonuç, veri hazırlığı, tutar kontrolü), dönem/entity özeti"
                              " ve varsa farkların dökümü"):
                with st.spinner("Excel raporu hazırlanıyor…"):
                    st.session_state["excel_bytes"] = to_excel_bytes(
                        filtered_x, diag, full=result,
                        stats=st.session_state.get("run_stats"), meta=st.session_state.get("run_meta"))
                    st.session_state["excel_sig"] = sig
                st.rerun()
        else:
            st.download_button(
                label="⬇️  Excel mutabakat raporunu indir",
                data=st.session_state["excel_bytes"],
                file_name=f"HYP_BW_mutabakat_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                type="primary",
                use_container_width=True,
            )

else:
    st.info("👆 Upload both files above to start the comparison.")
