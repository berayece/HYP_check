# HYP_check — Hyperion vs BW Comparison Tool

A web-based tool to compare Hyperion `.dat` exports against SAP BW Excel backups and identify discrepancies.

---

## Features

- Upload Hyperion `.dat` file and BW Excel file directly in the browser
- Auto-detects encoding (UTF-16), delimiter, and header rows
- Normalises period names (Jan → 1), ICP None variants, and case differences before matching
- Compares absolute values — opposite signs with equal amounts count as matching
- Classifies discrepancies into 3 categories:
  - 🟠 **HYP only** — row exists in Hyperion but not in BW
  - 🔵 **BW only** — row exists in BW but not in Hyperion
  - 🔴 **Value mismatch** — absolute amounts differ
- Excludes zero-value rows from comparison
- Filter results by Status, Scenario, Year, Period, Entity, Account, ICP, Custom columns
- Click any discrepancy row to see all raw records from both sources side by side
- Download filtered results as colour-coded Excel report

---

## Requirements

- Python 3.9 or later → [python.org](https://www.python.org/downloads/)

---

## Quick Start

### Mac
1. Download this repo as ZIP → unzip
2. Double-click **`start.command`**
3. Browser opens automatically at `http://localhost:8502`

### Windows
1. Download this repo as ZIP → unzip
2. Double-click **`start.bat`**
3. Browser opens automatically at `http://localhost:8502`

> Dependencies are installed automatically on first run.

---

## File Formats

### Hyperion `.dat` file
| Property | Value |
|---|---|
| Encoding | UTF-16 |
| Delimiter | Semicolon (`;`) |
| Header | None — first line is `! DATA` |
| Columns | Scenario, Year, Period, View, Entity, Value, Account, ICP, Custom1, Custom2, Custom3, Custom4, Amount |

### BW Excel file
| Property | Value |
|---|---|
| Format | `.xlsx` |
| Header row | Auto-detected (row 1 or row 2) |
| Key columns | CALYEAR, MONTH, /BIC/SCNRBI, /BIC/COMPBI, /BIC/ACCOUNTBI, /BIC/ICPBI, /BIC/CUSTOM1-4BI |
| Value column | CUM_VALUE |

---

## Column Mapping

| Hyperion | BW | Notes |
|---|---|---|
| Scenario | /BIC/SCNRBI | Case-insensitive |
| Year | CALYEAR | |
| Period | MONTH | Jan/Feb/… ↔ 1/2/… |
| Entity | /BIC/COMPBI | |
| Account | /BIC/ACCOUNTBI | |
| ICP | /BIC/ICPBI | `[ICP None]`, `[None]`, `NONE` → unified |
| Custom1 | /BIC/CUSTOM1BI | |
| Custom2 | /BIC/CUSTOM2BI | |
| Custom3 | /BIC/CUSTOM3BI | |
| Custom4 | /BIC/CUSTOM4BI | |
| **Amount** | **CUM_VALUE** | Numeric comparison |

Skipped columns: `View`, `Value` (HYP) · `MTD_VALUE`, `/BIC/CURBI`, `/BIC/CURTYPE`, `/BIC/ICPFLAG` (BW)

---

## Run Manually

```bash
pip install -r requirements.txt
streamlit run app.py
```
