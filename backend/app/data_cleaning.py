"""
Data resilience layer.

This module encodes everything we observed while inspecting the real
Skylark sample exports:

  - Stray embedded header rows (e.g. a data row where `Deal Status` ==
    "Deal Status") — an artifact of how the sheet was exported/pasted.
  - Free-text sector labels with inconsistent variants.
  - Multiple date formats, and date columns that are frequently blank.
  - Currency/quantity fields that arrive as text with commas, units
    ("5360 HA"), or are simply empty.
  - Placeholder nulls: "", "NONE", "N/A", "nan", "-", "TBD".

Every cleaning function is defensive: it never raises on bad input, it
degrades to None/NaN and lets the caller decide how to communicate that
gap to the user (see query_engine.data_quality_notes).
"""
import re
import pandas as pd
from dateutil import parser as dateparser

NULL_TOKENS = {"", "none", "n/a", "na", "nan", "-", "--", "tbd", "null", "unknown"}

# Canonical sector names <- observed variants (extend as new variants show up)
SECTOR_CANON = {
    "mining": "Mining",
    "renewables": "Renewables",
    "renewable": "Renewables",
    "railways": "Railways",
    "railway": "Railways",
    "powerline": "Powerline",
    "power line": "Powerline",
    "construction": "Construction",
    "dsp": "DSP",
    "tender": "Tender",
    "manufacturing": "Manufacturing",
    "security and surveillance": "Security & Surveillance",
    "aviation": "Aviation",
    "others": "Other",
    "other": "Other",
}


def is_null_token(val) -> bool:
    if val is None:
        return True
    if isinstance(val, float) and pd.isna(val):
        return True
    s = str(val).strip().lower()
    return s in NULL_TOKENS or s == ""


def clean_text_field(val):
    if is_null_token(val):
        return None
    return str(val).strip()


def clean_sector(val):
    if is_null_token(val):
        return None
    key = str(val).strip().lower()
    return SECTOR_CANON.get(key, str(val).strip().title())


def clean_number(val):
    """Strip currency symbols, commas, and trailing units (e.g. '5360 HA' -> 5360)."""
    if is_null_token(val):
        return None
    s = str(val).strip()
    s = re.sub(r"[₹$,]", "", s)
    match = re.match(r"^-?\d+(\.\d+)?", s)
    if not match:
        return None
    try:
        return float(match.group())
    except ValueError:
        return None


def clean_date(val):
    """Parse loosely-formatted dates; return ISO date string or None."""
    if is_null_token(val):
        return None
    try:
        dt = dateparser.parse(str(val), dayfirst=False, fuzzy=True)
        return dt.date().isoformat()
    except (ValueError, OverflowError, TypeError):
        return None


def drop_stray_header_rows(df: pd.DataFrame) -> pd.DataFrame:
    """
    Row artifact seen in both sheets: a data row whose cell value equals
    the column's own header text (e.g. Deal Status == 'Deal Status'),
    left over from a copy-paste of the header row into the body.
    """
    if df.empty:
        return df
    mask = pd.Series(False, index=df.index)
    for col in df.columns:
        mask = mask | (df[col].astype(str).str.strip() == col)
    return df[~mask].reset_index(drop=True)


def clean_deals(raw_rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(raw_rows)
    if df.empty:
        return df
    df = drop_stray_header_rows(df)

    text_cols = ["Deal Name", "Owner code", "Client Code", "Deal Status",
                 "Closure Probability", "Deal Stage", "Product deal"]
    for c in text_cols:
        if c in df.columns:
            df[c] = df[c].apply(clean_text_field)

    if "Sector/service" in df.columns:
        df["Sector"] = df["Sector/service"].apply(clean_sector)

    for c in ["Close Date (A)", "Tentative Close Date", "Created Date"]:
        if c in df.columns:
            df[c] = df[c].apply(clean_date)

    if "Masked Deal value" in df.columns:
        df["Deal Value"] = df["Masked Deal value"].apply(clean_number)

    # Normalize deal status casing/typos
    if "Deal Status" in df.columns:
        df["Deal Status"] = df["Deal Status"].apply(
            lambda v: v.title() if isinstance(v, str) and v else None
        )

    return df


def clean_work_orders(raw_rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(raw_rows)
    if df.empty:
        return df
    df = drop_stray_header_rows(df)

    text_cols = ["Deal name masked", "Customer Name Code", "Nature of Work",
                 "Execution Status", "Document Type", "BD/KAM Personnel code",
                 "Type of Work", "Invoice Status", "Billing Status",
                 "WO Status (billed)"]
    for c in text_cols:
        if c in df.columns:
            df[c] = df[c].apply(clean_text_field)

    if "Sector" in df.columns:
        df["Sector"] = df["Sector"].apply(clean_sector)

    date_cols = ["Data Delivery Date", "Date of PO/LOI", "Probable Start Date",
                 "Probable End Date", "Last invoice date", "Collection Date"]
    for c in date_cols:
        if c in df.columns:
            df[c] = df[c].apply(clean_date)

    money_cols = [
        "Amount in Rupees (Excl of GST) (Masked)",
        "Amount in Rupees (Incl of GST) (Masked)",
        "Billed Value in Rupees (Excl of GST.) (Masked)",
        "Billed Value in Rupees (Incl of GST.) (Masked)",
        "Collected Amount in Rupees (Incl of GST.) (Masked)",
        "Amount to be billed in Rs. (Exl. of GST) (Masked)",
        "Amount to be billed in Rs. (Incl. of GST) (Masked)",
        "Amount Receivable (Masked)",
    ]
    for c in money_cols:
        if c in df.columns:
            df[c] = df[c].apply(clean_number)

    qty_cols = ["Quantity by Ops", "Quantities as per PO",
                "Quantity billed (till date)", "Balance in quantity"]
    for c in qty_cols:
        if c in df.columns:
            df[c] = df[c].apply(clean_number)

    return df


def data_quality_report(df: pd.DataFrame, label: str, key_columns: list[str]) -> dict:
    """Summarize completeness of key columns so the agent can caveat answers."""
    if df.empty:
        return {"board": label, "row_count": 0, "issues": ["No rows returned from monday.com."]}
    issues = []
    total = len(df)
    for col in key_columns:
        if col not in df.columns:
            issues.append(f"Column '{col}' not found on the {label} board.")
            continue
        missing = df[col].isna().sum()
        if missing > 0:
            pct = round(100 * missing / total, 1)
            issues.append(f"'{col}' is missing on {missing}/{total} rows ({pct}%).")
    return {"board": label, "row_count": total, "issues": issues}
