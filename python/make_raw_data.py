"""
make_raw_data.py  —  (supporting utility, not the main deliverable)

Builds the *raw* practice file `data/raw_nbfc_portfolio.csv` that the ETL script
cleans. It creates a plain NBFC panel whose sector GNPA ties to the RBI control
totals, then deliberately injects the everyday data-quality problems you get from a
real source export — duplicate rows, stray whitespace, inconsistent spellings,
missing-value tokens, numbers stored as text ("1,234" / "3.4%"), impossible values,
and mixed date formats.

You normally do NOT need to run this — the messy CSV is already in data/. It's here
so the repo is reproducible and so you can see exactly what "dirty data" was created.

Run (optional):
    python make_raw_data.py
"""
from pathlib import Path
import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = HERE.parents[0] / "data"
RNG = np.random.default_rng(7)                       # fixed seed -> same file every run

QUARTERS = ["FY21Q4", "FY22Q2", "FY22Q4", "FY23Q2", "FY23Q4",
            "FY24Q2", "FY24Q4", "FY25Q2", "FY25Q4"]
QEND = {"FY21Q4": "2021-03-31", "FY22Q2": "2021-09-30", "FY22Q4": "2022-03-31",
        "FY23Q2": "2022-09-30", "FY23Q4": "2023-03-31", "FY24Q2": "2023-09-30",
        "FY24Q4": "2024-03-31", "FY25Q2": "2024-09-30", "FY25Q4": "2025-03-31"}
LAYERS = ["Upper Layer", "Middle Layer", "Base Layer"]
SECTORS = ["Retail (Vehicle)", "Retail (Housing)", "Retail (Gold)",
           "Retail (Personal/Unsecured)", "Microfinance", "MSME",
           "Infrastructure", "Wholesale/Corporate"]

LAYER_SHARE = {"Upper Layer": 0.302, "Middle Layer": 0.646, "Base Layer": 0.052}
LAYER_N = {"Upper Layer": 15, "Middle Layer": 25, "Base Layer": 20}
SECTOR_RISK = {"Retail (Vehicle)": 1.05, "Retail (Housing)": 0.55,
               "Retail (Gold)": 0.35, "Retail (Personal/Unsecured)": 1.75,
               "Microfinance": 2.10, "MSME": 1.30, "Infrastructure": 0.90,
               "Wholesale/Corporate": 1.15}
TILT = {"Upper Layer":  [0.20, 0.22, 0.06, 0.10, 0.04, 0.12, 0.14, 0.12],
        "Middle Layer": [0.16, 0.14, 0.10, 0.14, 0.12, 0.16, 0.08, 0.10],
        "Base Layer":   [0.14, 0.08, 0.18, 0.18, 0.20, 0.14, 0.02, 0.06]}
REGIONS = ["West", "South", "North", "East", "Central"]


def build_clean_panel() -> pd.DataFrame:
    """A plain, tidy NBFC x quarter x sector panel anchored to the RBI totals."""
    controls = pd.read_csv(DATA / "rbi_sector_controls.csv").set_index("quarter")

    # --- entity master: 60 NBFCs, simple size weights within each layer ---
    ents, c = [], 1
    for layer, n in LAYER_N.items():
        w = RNG.uniform(0.5, 1.5, n)
        w = w / w.sum()
        for i in range(n):
            ents.append({"nbfc_id": f"NBFC{c:03d}",
                         "nbfc_name": f"{layer.split()[0]}Fin {c:02d} Ltd",
                         "layer": layer, "region": REGIONS[c % 5],
                         "weight": w[i]})
            c += 1
    dim = pd.DataFrame(ents)

    # --- spread the published totals across entities and sectors ---
    rows = []
    for q in QUARTERS:
        total = float(controls.loc[q, "total_assets_cr"])
        for layer in LAYERS:
            layer_assets = total * LAYER_SHARE[layer]
            tilt = np.array(TILT[layer])
            for r in dim[dim.layer == layer].itertuples():
                ent_assets = layer_assets * r.weight
                for si, sector in enumerate(SECTORS):
                    gnpa = (controls.loc[q, "gnpa_pct"] * SECTOR_RISK[sector]
                            * RNG.uniform(0.95, 1.05))
                    rows.append({"nbfc_id": r.nbfc_id, "nbfc_name": r.nbfc_name,
                                 "layer": layer, "region": r.region, "quarter": q,
                                 "period_end": QEND[q], "sector": sector,
                                 "loan_outstanding_cr": round(ent_assets * tilt[si], 2),
                                 "gnpa_raw": gnpa})
    df = pd.DataFrame(rows)

    # scale each quarter's GNPA so the loan-weighted sector figure hits the RBI total
    for q in QUARTERS:
        m = df.quarter == q
        target = float(controls.loc[q, "gnpa_pct"])
        implied = (df.loc[m, "gnpa_raw"] * df.loc[m, "loan_outstanding_cr"]).sum() \
            / df.loc[m, "loan_outstanding_cr"].sum()
        df.loc[m, "gnpa_pct"] = (df.loc[m, "gnpa_raw"] * target / implied).round(3)

    # net NPA from a coverage ratio that improves over time; capital & profitability
    pcr = {q: 0.55 + 0.03 * i for i, q in enumerate(QUARTERS)}
    df["nnpa_pct"] = (df["gnpa_pct"] * (1 - df["quarter"].map(pcr))).round(3)
    ctrl = pd.read_csv(DATA / "rbi_sector_controls.csv").set_index("quarter")
    df["crar_pct"] = (df["quarter"].map(ctrl["crar_pct"])
                      + RNG.uniform(-3, 3, len(df))).round(2)
    df["roa_pct"] = (df["quarter"].map(ctrl["roa_pct"])
                     + RNG.uniform(-0.6, 0.6, len(df))).round(3)

    return df[["nbfc_id", "nbfc_name", "layer", "region", "quarter", "period_end",
               "sector", "loan_outstanding_cr", "gnpa_pct", "nnpa_pct",
               "crar_pct", "roa_pct"]]


def dirty(df: pd.DataFrame) -> pd.DataFrame:
    """Inject realistic data-quality problems into the clean panel."""
    raw = df.astype(object).copy()            # object dtype so we can drop in text tokens
    n = len(raw)

    def pick(frac):                            # helper: random row indices
        return RNG.choice(n, int(n * frac), replace=False)

    # 1) stray whitespace around text values
    for col in ["nbfc_id", "layer", "sector"]:
        for i in pick(0.05):
            raw.at[i, col] = f"  {raw.at[i, col]} "

    # 2) inconsistent spellings / casing for categories
    layer_variants = {"Upper Layer": ["upper layer", "UPPER LAYER", "UL"],
                      "Middle Layer": ["middle layer", "Middle  Layer", "ML"],
                      "Base Layer": ["base layer", "BASE LAYER", "BL"]}
    sector_variants = {"Microfinance": ["MFI", "micro finance", "microfinance"],
                       "MSME": ["msme", "M.S.M.E"],
                       "Retail (Housing)": ["housing", "retail (housing)"],
                       "Retail (Gold)": ["gold", "GOLD LOAN"]}
    for i in pick(0.20):
        v = str(raw.at[i, "layer"]).strip()
        if v in layer_variants:
            raw.at[i, "layer"] = RNG.choice(layer_variants[v])
    for i in pick(0.20):
        v = str(raw.at[i, "sector"]).strip()
        if v in sector_variants:
            raw.at[i, "sector"] = RNG.choice(sector_variants[v])

    # 3) missing-value tokens sprinkled into the metric columns
    for col in ["gnpa_pct", "nnpa_pct", "crar_pct", "roa_pct"]:
        for i in pick(0.03):
            raw.at[i, col] = RNG.choice(["N/A", "NA", "-", "", "null"])

    # 4) numbers stored as text: thousands commas and stray percent signs
    for i in pick(0.15):
        raw.at[i, "loan_outstanding_cr"] = f"{float(raw.at[i, 'loan_outstanding_cr']):,.2f}"
    for col in ["gnpa_pct", "crar_pct"]:
        for i in pick(0.08):
            try:
                raw.at[i, col] = f"{float(raw.at[i, col])}%"
            except (ValueError, TypeError):
                pass

    # 5) impossible / out-of-range values
    for i in pick(0.01):
        raw.at[i, "gnpa_pct"] = RNG.choice([250, -5, 999])       # GNPA% can't be these
    for i in pick(0.01):
        raw.at[i, "loan_outstanding_cr"] = RNG.choice([-100, 0]) # book can't be <= 0
    for i in pick(0.01):
        raw.at[i, "crar_pct"] = 999
    for i in pick(0.01):                                          # NNPA% > GNPA% (corrupt)
        raw.at[i, "nnpa_pct"] = float(pd.to_numeric(raw.at[i, "gnpa_pct"],
                                                    errors="coerce") or 5) + 3

    # 6) mixed date formats
    d = pd.to_datetime(raw["period_end"])
    fmt = RNG.choice(["%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"], size=n)
    raw["period_end"] = [dt.strftime(f) for dt, f in zip(d, fmt)]

    # 7) duplicate rows: ~200 exact copies + ~50 key-duplicates with tweaked numbers
    exact = raw.iloc[RNG.choice(n, 200, replace=False)].copy()
    keydup = raw.iloc[RNG.choice(n, 50, replace=False)].copy()
    for i in keydup.index:
        val = pd.to_numeric(str(keydup.at[i, "loan_outstanding_cr"]).replace(",", ""),
                            errors="coerce")
        if pd.notna(val):
            keydup.at[i, "loan_outstanding_cr"] = round(val * 1.02, 2)
    raw = pd.concat([raw, exact, keydup], ignore_index=True)

    # 8) a couple of completely blank rows
    blank = pd.DataFrame([{c: "" for c in raw.columns} for _ in range(3)])
    raw = pd.concat([raw, blank], ignore_index=True)

    # shuffle so the problems aren't all at the bottom
    return raw.sample(frac=1, random_state=7).reset_index(drop=True)


def main() -> None:
    DATA.mkdir(exist_ok=True)
    clean = build_clean_panel()
    raw = dirty(clean)
    out = DATA / "raw_nbfc_portfolio.csv"
    raw.to_csv(out, index=False)
    print(f"wrote {out}  ({len(raw):,} rows, including injected duplicates & errors)")


if __name__ == "__main__":
    main()
