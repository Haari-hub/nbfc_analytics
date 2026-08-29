"""
load_db.py — build nbfc.db from the generated CSVs (portable alternative to the
SQLite CLI `.import` commands documented in nbfc_analytics.sql Section 0).

Usage:
    python load_db.py            # creates ./nbfc.db next to this script
"""
from pathlib import Path
import sqlite3
import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = HERE.parents[0] / "data"
DB = HERE / "nbfc.db"

SCHEMA = """
DROP TABLE IF EXISTS fact_portfolio;
DROP TABLE IF EXISTS dim_nbfc;
DROP TABLE IF EXISTS agg_sector_quarter;

CREATE TABLE dim_nbfc (
    nbfc_id TEXT PRIMARY KEY, nbfc_name TEXT NOT NULL, layer TEXT NOT NULL,
    region TEXT
);
CREATE TABLE fact_portfolio (
    nbfc_id TEXT NOT NULL, layer TEXT NOT NULL, quarter TEXT NOT NULL,
    period_end TEXT NOT NULL, sector TEXT NOT NULL, loan_outstanding_cr REAL NOT NULL,
    gnpa_pct REAL, gnpa_amount_cr REAL, nnpa_pct REAL, nnpa_amount_cr REAL,
    pcr REAL, crar_pct REAL, roa_pct REAL,
    PRIMARY KEY (nbfc_id, quarter, sector),
    FOREIGN KEY (nbfc_id) REFERENCES dim_nbfc (nbfc_id)
);
CREATE TABLE agg_sector_quarter (
    grouping TEXT NOT NULL, dimension_value TEXT NOT NULL, quarter TEXT NOT NULL,
    loan_outstanding_cr REAL, gnpa_pct_wtd REAL, nnpa_pct_wtd REAL,
    crar_pct_wtd REAL, roa_pct_wtd REAL
);
CREATE INDEX ix_fact_qtr    ON fact_portfolio (quarter);
CREATE INDEX ix_fact_sector ON fact_portfolio (sector, quarter);
CREATE INDEX ix_fact_nbfc   ON fact_portfolio (nbfc_id, period_end);
"""


def main() -> None:
    conn = sqlite3.connect(DB)
    conn.executescript(SCHEMA)
    for table in ["dim_nbfc", "fact_portfolio", "agg_sector_quarter"]:
        df = pd.read_csv(DATA / f"{table}.csv")
        df.to_sql(table, conn, if_exists="append", index=False)
        print(f"loaded {table}: {len(df):,} rows")
    conn.commit()
    conn.close()
    print(f"built {DB}")


if __name__ == "__main__":
    main()
