"""
etl_nbfc.py
===========
ETL pipeline for the Indian NBFC portfolio dataset.

    EXTRACT  ->  CLEAN  ->  DEDUPLICATE  ->  VALIDATE  ->  LOAD

Reads the raw, messy export (data/raw_nbfc_portfolio.csv), fixes the usual
data-quality problems an analyst runs into, checks the result against a set of
business rules, and writes clean, analysis-ready tables for SQL and Power BI.

The whole script is deliberately linear and readable: one function per stage, plain
pandas, and a comment on every cleaning decision so it can be walked through easily.

Run:
    python etl_nbfc.py

Outputs (to ../data/):
    fact_portfolio.csv        clean NBFC x quarter x sector facts (the main table)
    dim_nbfc.csv              one row per NBFC (built from the cleaned data)
    agg_sector_quarter.csv    layer / sector roll-up for the dashboard
    data_quality_report.csv   what was wrong and what the ETL did about it
"""

from pathlib import Path
import numpy as np
import pandas as pd

DATA = Path(__file__).resolve().parents[1] / "data"
RAW_FILE = DATA / "raw_nbfc_portfolio.csv"

# Canonical category labels we standardise everything to.
VALID_LAYERS = ["Upper Layer", "Middle Layer", "Base Layer"]
VALID_SECTORS = ["Retail (Vehicle)", "Retail (Housing)", "Retail (Gold)",
                 "Retail (Personal/Unsecured)", "Microfinance", "MSME",
                 "Infrastructure", "Wholesale/Corporate"]
NUMERIC_COLS = ["loan_outstanding_cr", "gnpa_pct", "nnpa_pct", "crar_pct", "roa_pct"]

# Anything matching these (after trimming + lowercasing) means "missing".
MISSING_TOKENS = {"", "na", "n/a", "-", "null", "none", "nan"}

# Map each FY-quarter to its period-end date, used to repair unparseable dates.
QUARTER_END = {"FY21Q4": "2021-03-31", "FY22Q2": "2021-09-30", "FY22Q4": "2022-03-31",
               "FY23Q2": "2022-09-30", "FY23Q4": "2023-03-31", "FY24Q2": "2023-09-30",
               "FY24Q4": "2024-03-31", "FY25Q2": "2024-09-30", "FY25Q4": "2025-03-31"}

# Collects one row per data-quality issue for the final report.
QUALITY_LOG: list[dict] = []


def log_issue(check: str, rows: int, action: str) -> None:
    """Record a data-quality finding so we can write a report at the end."""
    QUALITY_LOG.append({"check": check, "rows_affected": int(rows), "action": action})


# --------------------------------------------------------------------------- #
# 1. EXTRACT
# --------------------------------------------------------------------------- #
def extract() -> pd.DataFrame:
    """Read the raw CSV as text.

    We load every column as a string (dtype=str) and switch off pandas' automatic
    NA handling. That way WE decide what counts as missing and how each column is
    parsed, instead of pandas silently guessing types on dirty data.
    """
    df = pd.read_csv(RAW_FILE, dtype=str, keep_default_na=False)
    print(f"[extract] read {len(df):,} raw rows from {RAW_FILE.name}")
    return df


# --------------------------------------------------------------------------- #
# 2. CLEAN
# --------------------------------------------------------------------------- #
def clean(df: pd.DataFrame) -> pd.DataFrame:
    """Fix whitespace, categories, missing tokens, number formats and dates."""

    # 2a. Drop rows that are completely empty (the blank junk rows in the export).
    before = len(df)
    df = df.replace(r"^\s*$", np.nan, regex=True)          # blank/whitespace -> NaN
    df = df.dropna(how="all")
    log_issue("empty rows", before - len(df), "dropped")

    # 2b. Trim leading/trailing whitespace on every text column.
    text_cols = ["nbfc_id", "nbfc_name", "layer", "region", "quarter", "sector"]
    for col in text_cols:
        df[col] = df[col].astype(str).str.strip()

    # 2c. Turn every "missing" token (NA, N/A, -, null, blank) into a real NaN.
    def blank_if_missing(x):
        return np.nan if str(x).strip().lower() in MISSING_TOKENS else x
    n_missing = df[NUMERIC_COLS].map(
        lambda x: str(x).strip().lower() in MISSING_TOKENS).sum().sum()
    df = df.map(blank_if_missing)
    log_issue("missing-value tokens (N/A, -, null, blank)", n_missing,
              "converted to NaN")

    # 2d. Standardise category spellings to the canonical labels.
    #     We match on a keyword so 'ML', 'middle layer', 'Middle  Layer' all map.
    df["layer"] = df["layer"].apply(standardise_layer)
    df["sector"] = df["sector"].apply(standardise_sector)
    log_issue("non-standard layer/sector spellings",
              (df["layer"].isna() | df["sector"].isna()).sum(),
              "unmapped values set to NaN (dropped in validation)")

    # 2e. Convert the numeric columns from text to numbers.
    #     Strip thousands-commas and stray % signs first, then coerce; anything
    #     that still can't be parsed becomes NaN.
    for col in NUMERIC_COLS:
        df[col] = to_number(df[col])

    # 2f. Parse dates from the mixed formats, then repair any that failed using
    #     the quarter label. Store back as a clean ISO (YYYY-MM-DD) string.
    parsed = pd.to_datetime(df["period_end"], errors="coerce",
                            dayfirst=True, format="mixed")
    bad_dates = parsed.isna().sum()
    parsed = parsed.fillna(df["quarter"].map(QUARTER_END).apply(pd.Timestamp))
    df["period_end"] = parsed.dt.strftime("%Y-%m-%d")
    log_issue("unparseable dates", bad_dates, "repaired from quarter label")

    print(f"[clean]   standardised text, numbers and dates; {len(df):,} rows remain")
    return df


def standardise_layer(value) -> float | str:
    """Map any layer spelling to one of the three canonical SBR layers."""
    if pd.isna(value):
        return np.nan
    k = str(value).strip().lower()
    if "upper" in k or k == "ul":
        return "Upper Layer"
    if "middle" in k or k == "ml":
        return "Middle Layer"
    if "base" in k or k == "bl":
        return "Base Layer"
    return np.nan                       # unknown -> flag as missing


def standardise_sector(value) -> float | str:
    """Map any sector spelling to one of the eight canonical loan segments."""
    if pd.isna(value):
        return np.nan
    k = str(value).strip().lower()
    rules = [
        (("mfi", "micro"), "Microfinance"),
        (("msme",), "MSME"),
        (("gold",), "Retail (Gold)"),
        (("housing",), "Retail (Housing)"),
        (("vehicle",), "Retail (Vehicle)"),
        (("personal", "unsecured"), "Retail (Personal/Unsecured)"),
        (("infra",), "Infrastructure"),
        (("wholesale", "corporate"), "Wholesale/Corporate"),
    ]
    for keywords, canonical in rules:
        if any(word in k for word in keywords):
            return canonical
    return np.nan


def to_number(series: pd.Series) -> pd.Series:
    """Clean a text column into numbers: remove commas and %, then coerce."""
    cleaned = (series.astype(str)
               .str.replace(",", "", regex=False)
               .str.replace("%", "", regex=False)
               .str.strip())
    return pd.to_numeric(cleaned, errors="coerce")


# --------------------------------------------------------------------------- #
# 3. DEDUPLICATE
# --------------------------------------------------------------------------- #
def deduplicate(df: pd.DataFrame) -> pd.DataFrame:
    """Remove duplicate records in two passes."""

    # 3a. Exact duplicates — identical rows repeated in the export.
    before = len(df)
    df = df.drop_duplicates()
    log_issue("exact duplicate rows", before - len(df), "dropped")

    # 3b. Business-key duplicates — the same NBFC/quarter/sector appearing more
    #     than once with slightly different numbers. One entity can only have one
    #     record per sector per quarter, so we keep the first and drop the rest.
    key = ["nbfc_id", "quarter", "sector"]
    before = len(df)
    df = df.sort_values(key).drop_duplicates(subset=key, keep="first")
    log_issue("duplicate NBFC/quarter/sector keys", before - len(df),
              "kept first, dropped rest")

    print(f"[dedupe]  removed duplicates; {len(df):,} unique records remain")
    return df


# --------------------------------------------------------------------------- #
# 4. VALIDATE
# --------------------------------------------------------------------------- #
def validate(df: pd.DataFrame) -> pd.DataFrame:
    """Apply business rules: drop unusable rows, null-out impossible values,
    then fill the few remaining gaps so downstream tables are complete."""

    # 4a. Critical keys must be present — no ID, quarter, sector or loan = unusable.
    before = len(df)
    df = df.dropna(subset=["nbfc_id", "quarter", "sector", "loan_outstanding_cr"])
    log_issue("rows missing a critical field", before - len(df), "dropped")

    # 4b. A loan book must be positive. Zero/negative can't be analysed.
    before = len(df)
    df = df[df["loan_outstanding_cr"] > 0]
    log_issue("non-positive loan_outstanding_cr", before - len(df), "dropped")

    # 4c. Ratios must sit in sensible ranges. Out-of-range = data error -> NaN
    #     (we don't trust the value, but we keep the rest of the row).
    ranges = {"gnpa_pct": (0, 100), "nnpa_pct": (0, 100),
              "crar_pct": (0, 100), "roa_pct": (-50, 50)}
    for col, (lo, hi) in ranges.items():
        bad = ~df[col].between(lo, hi) & df[col].notna()
        if bad.any():
            log_issue(f"{col} outside [{lo}, {hi}]", bad.sum(), "set to NaN")
            df.loc[bad, col] = np.nan

    # 4d. Business rule: Net NPA can never exceed Gross NPA. Such rows are
    #     internally inconsistent, so we drop them.
    before = len(df)
    inconsistent = df["nnpa_pct"] > df["gnpa_pct"]
    df = df[~inconsistent.fillna(False)]
    log_issue("NNPA% greater than GNPA%", before - len(df), "dropped")

    # 4e. Fill the small number of remaining NaN ratios with the median of their
    #     (quarter, sector) peer group — preserves sample size without inventing
    #     outliers. Fall back to the column median if a group has no data.
    for col in ["gnpa_pct", "nnpa_pct", "crar_pct", "roa_pct"]:
        n_missing = df[col].isna().sum()
        if n_missing:
            df[col] = df.groupby(["quarter", "sector"])[col] \
                        .transform(lambda s: s.fillna(s.median()))
            df[col] = df[col].fillna(df[col].median())
            log_issue(f"missing {col} after cleaning", n_missing,
                      "imputed with quarter/sector median")

    print(f"[validate] applied business rules; {len(df):,} valid records remain")
    return df


# --------------------------------------------------------------------------- #
# 5. DERIVE + LOAD
# --------------------------------------------------------------------------- #
def add_derived_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Add the simple calculated columns the dashboard needs.

    (Trend metrics that need a previous quarter — credit growth, slippage — are
    left to the SQL/Power BI layer, where window functions handle them cleanly.)
    """
    df["gnpa_amount_cr"] = (df["loan_outstanding_cr"] * df["gnpa_pct"] / 100).round(2)
    df["nnpa_amount_cr"] = (df["loan_outstanding_cr"] * df["nnpa_pct"] / 100).round(2)
    # Provision coverage = share of gross NPAs already provided for. Undefined
    # when there are no gross NPAs, so guard the divide-by-zero.
    df["pcr"] = np.where(df["gnpa_amount_cr"] > 0,
                         (1 - df["nnpa_amount_cr"] / df["gnpa_amount_cr"]).round(3),
                         np.nan)
    return df


def build_outputs(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Split the clean data into a small star schema for SQL / Power BI."""

    # Fact table — the grain everything is measured at.
    fact_cols = ["nbfc_id", "layer", "quarter", "period_end", "sector",
                 "loan_outstanding_cr", "gnpa_pct", "gnpa_amount_cr",
                 "nnpa_pct", "nnpa_amount_cr", "pcr", "crar_pct", "roa_pct"]
    fact = df[fact_cols].sort_values(["nbfc_id", "period_end", "sector"])

    # Dimension — one row per NBFC, built from the cleaned records.
    dim = (df[["nbfc_id", "nbfc_name", "layer", "region"]]
           .drop_duplicates("nbfc_id")
           .sort_values("nbfc_id"))

    # Aggregate — loan-weighted roll-ups by layer and by sector, per quarter.
    agg = build_aggregate(df)
    return fact, dim, agg


def build_aggregate(df: pd.DataFrame) -> pd.DataFrame:
    """Loan-weighted GNPA/NNPA/CRAR/RoA by (layer, quarter) and (sector, quarter)."""
    def weighted_avg(frame, value_col):
        w = frame["loan_outstanding_cr"]
        return (frame[value_col] * w).sum() / w.sum()

    parts = []
    for grouping, key in [("layer", "layer"), ("sector", "sector")]:
        g = (df.groupby([key, "quarter"])
             .apply(lambda f: pd.Series({
                 "loan_outstanding_cr": round(f["loan_outstanding_cr"].sum(), 2),
                 "gnpa_pct_wtd": round(weighted_avg(f, "gnpa_pct"), 3),
                 "nnpa_pct_wtd": round(weighted_avg(f, "nnpa_pct"), 3),
                 "crar_pct_wtd": round(weighted_avg(f, "crar_pct"), 3),
                 "roa_pct_wtd": round(weighted_avg(f, "roa_pct"), 3),
             }), include_groups=False)
             .reset_index()
             .rename(columns={key: "dimension_value"}))
        g.insert(0, "grouping", grouping)
        parts.append(g)
    return pd.concat(parts, ignore_index=True)


def load(fact: pd.DataFrame, dim: pd.DataFrame, agg: pd.DataFrame) -> None:
    """Write the clean tables and the data-quality report to ../data/."""
    fact.to_csv(DATA / "fact_portfolio.csv", index=False)
    dim.to_csv(DATA / "dim_nbfc.csv", index=False)
    agg.to_csv(DATA / "agg_sector_quarter.csv", index=False)
    pd.DataFrame(QUALITY_LOG).to_csv(DATA / "data_quality_report.csv", index=False)
    print(f"[load]    wrote fact ({len(fact):,}), dim ({len(dim)}), "
          f"agg ({len(agg)}) + data_quality_report.csv")


# --------------------------------------------------------------------------- #
# Reconciliation check (nice sanity test: does the clean data still match RBI?)
# --------------------------------------------------------------------------- #
def reconcile_to_rbi(fact: pd.DataFrame) -> None:
    """Compare the cleaned sector GNPA% against the published RBI control totals."""
    controls = pd.read_csv(DATA / "rbi_sector_controls.csv").set_index("quarter")
    print("\n[check]   cleaned sector GNPA% vs RBI published figure:")
    print(f"          {'quarter':<9}{'cleaned':>9}{'rbi':>7}{'diff':>7}")
    for q in controls.index:
        sub = fact[fact["quarter"] == q]
        if sub.empty:
            continue
        cleaned = (sub["gnpa_amount_cr"].sum() / sub["loan_outstanding_cr"].sum()) * 100
        rbi = controls.loc[q, "gnpa_pct"]
        print(f"          {q:<9}{cleaned:>9.2f}{rbi:>7.2f}{cleaned - rbi:>+7.2f}")


# --------------------------------------------------------------------------- #
# Main — run the pipeline end to end
# --------------------------------------------------------------------------- #
def main() -> None:
    print("=" * 60)
    print("NBFC ETL pipeline")
    print("=" * 60)

    df = extract()
    df = clean(df)
    df = deduplicate(df)
    df = validate(df)
    df = add_derived_columns(df)

    fact, dim, agg = build_outputs(df)
    load(fact, dim, agg)
    reconcile_to_rbi(fact)

    print("\nData-quality summary:")
    print(pd.DataFrame(QUALITY_LOG).to_string(index=False))
    print("\nDone. Clean tables are in ../data/ — load them with ../sql/load_db.py "
          "or straight into Power BI.")


if __name__ == "__main__":
    main()
