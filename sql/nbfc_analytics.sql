/* ==========================================================================
   nbfc_analytics.sql
   Indian NBFC Portfolio & Asset-Quality Analytics — SQL layer
   --------------------------------------------------------------------------
   Target engine : SQLite 3.35+ (uses generated columns + window functions).
                   Ports to Postgres/MySQL with only cosmetic changes
                   (notes inline where a dialect differs).

   HOW TO RUN (SQLite):
       sqlite3 nbfc.db < schema_and_load.sql   -- see Section 0 to build the DB
       sqlite3 nbfc.db < nbfc_analytics.sql     -- run the analysis below

   The three CSVs produced by ../python/build_nbfc_dataset.py are the inputs:
       dim_nbfc.csv, fact_portfolio.csv, agg_sector_quarter.csv
   Section 0 shows the schema and the .import commands to load them.

   WHAT'S DEMONSTRATED
       - Star-schema modelling (one dimension, one fact, one pre-agg table)
       - Window functions: LAG, running totals, RANK, NTILE, moving averages
       - CTEs and layered aggregation for asset-quality KPIs
       - Cohort / vintage-style slippage analysis
       - Concentration risk (Herfindahl-Hirschman Index) in pure SQL
       - Data-quality assertions
   ========================================================================== */


/* ==========================================================================
   SECTION 0 — SCHEMA & LOAD  (run once to build nbfc.db)
   --------------------------------------------------------------------------
   Kept here as documentation. In practice put Section 0 in its own file
   (schema_and_load.sql) and pipe the CSVs in via the SQLite CLI.
   ========================================================================== */

-- DROP TABLE IF EXISTS fact_portfolio;
-- DROP TABLE IF EXISTS dim_nbfc;
-- DROP TABLE IF EXISTS agg_sector_quarter;

CREATE TABLE IF NOT EXISTS dim_nbfc (
    nbfc_id            TEXT PRIMARY KEY,
    nbfc_name          TEXT NOT NULL,
    layer              TEXT NOT NULL,          -- Upper / Middle / Base (SBR framework)
    region             TEXT
);

CREATE TABLE IF NOT EXISTS fact_portfolio (
    nbfc_id               TEXT NOT NULL,
    layer                 TEXT NOT NULL,
    quarter               TEXT NOT NULL,        -- e.g. 'FY24Q4'
    period_end            TEXT NOT NULL,        -- ISO date, sortable
    sector                TEXT NOT NULL,        -- loan bucket
    loan_outstanding_cr   REAL NOT NULL,        -- INR crore
    gnpa_pct              REAL,
    gnpa_amount_cr        REAL,
    nnpa_pct              REAL,
    nnpa_amount_cr        REAL,
    pcr                   REAL,                 -- provision coverage ratio (0-1)
    crar_pct              REAL,
    roa_pct               REAL,
    -- Note: QoQ credit-growth and slippage are NOT stored here; they need a prior
    -- quarter, so they are derived on the fly with window functions (Sections 2 & 6).
    PRIMARY KEY (nbfc_id, quarter, sector),
    FOREIGN KEY (nbfc_id) REFERENCES dim_nbfc (nbfc_id)
);

CREATE TABLE IF NOT EXISTS agg_sector_quarter (
    grouping           TEXT NOT NULL,           -- 'layer' or 'sector'
    dimension_value    TEXT NOT NULL,
    quarter            TEXT NOT NULL,
    loan_outstanding_cr REAL,
    gnpa_pct_wtd       REAL,
    nnpa_pct_wtd       REAL,
    crar_pct_wtd       REAL,
    roa_pct_wtd        REAL
);

-- Helpful indexes for the analytical queries below.
CREATE INDEX IF NOT EXISTS ix_fact_qtr     ON fact_portfolio (quarter);
CREATE INDEX IF NOT EXISTS ix_fact_sector  ON fact_portfolio (sector, quarter);
CREATE INDEX IF NOT EXISTS ix_fact_nbfc    ON fact_portfolio (nbfc_id, period_end);

/*  Load commands (run in the sqlite3 shell, not as SQL):

    .mode csv
    .import --skip 1 data/dim_nbfc.csv            dim_nbfc
    .import --skip 1 data/fact_portfolio.csv      fact_portfolio
    .import --skip 1 data/agg_sector_quarter.csv  agg_sector_quarter
*/


/* ==========================================================================
   SECTION 1 — SECTOR HEADLINE TREND
   Q: How did the NBFC sector's asset quality and capital evolve each quarter?
   Technique: loan-weighted aggregation (a simple AVG would over-weight tiny books).
   ========================================================================== */
SELECT
    quarter,
    ROUND(SUM(loan_outstanding_cr) / 1e5, 2)                      AS loan_book_lakh_cr,
    ROUND(SUM(gnpa_amount_cr)  * 100.0 / SUM(loan_outstanding_cr), 2) AS gnpa_pct,
    ROUND(SUM(nnpa_amount_cr)  * 100.0 / SUM(loan_outstanding_cr), 2) AS nnpa_pct,
    ROUND(1 - SUM(nnpa_amount_cr) / SUM(gnpa_amount_cr), 3)       AS implied_pcr,
    ROUND(SUM(crar_pct * loan_outstanding_cr) / SUM(loan_outstanding_cr), 1) AS crar_pct_wtd
FROM fact_portfolio
GROUP BY quarter
ORDER BY MIN(period_end);


/* ==========================================================================
   SECTION 2 — QoQ MOMENTUM WITH A WINDOW FUNCTION (LAG)
   Q: What was the change in sector GNPA% versus the previous quarter?
   Technique: LAG() over the ordered quarter series — the canonical use of a
   window function for period-over-period deltas.
   ========================================================================== */
WITH sector_q AS (
    SELECT
        quarter,
        MIN(period_end)                                              AS period_end,
        SUM(gnpa_amount_cr) * 100.0 / SUM(loan_outstanding_cr)       AS gnpa_pct
    FROM fact_portfolio
    GROUP BY quarter
)
SELECT
    quarter,
    ROUND(gnpa_pct, 2)                                              AS gnpa_pct,
    ROUND(LAG(gnpa_pct) OVER (ORDER BY period_end), 2)              AS prev_gnpa_pct,
    ROUND(gnpa_pct - LAG(gnpa_pct) OVER (ORDER BY period_end), 2)   AS qoq_change_pp
FROM sector_q
ORDER BY period_end;


/* ==========================================================================
   SECTION 3 — LAYER LEAGUE TABLE (Scale-Based Regulation view)
   Q: In the latest quarter, how do the three NBFC layers compare on the KPIs,
      and what share of the sector book does each hold?
   Technique: subquery for the latest quarter + share-of-total window sum.
   ========================================================================== */
WITH latest AS (
    SELECT quarter FROM fact_portfolio ORDER BY period_end DESC LIMIT 1
)
SELECT
    f.layer,
    ROUND(SUM(f.loan_outstanding_cr) / 1e5, 2)                     AS loan_book_lakh_cr,
    ROUND(100.0 * SUM(f.loan_outstanding_cr)
          / SUM(SUM(f.loan_outstanding_cr)) OVER (), 1)            AS pct_of_sector_book,
    ROUND(SUM(f.gnpa_amount_cr) * 100.0 / SUM(f.loan_outstanding_cr), 2) AS gnpa_pct,
    ROUND(SUM(f.nnpa_amount_cr) * 100.0 / SUM(f.loan_outstanding_cr), 2) AS nnpa_pct,
    ROUND(SUM(f.crar_pct * f.loan_outstanding_cr)
          / SUM(f.loan_outstanding_cr), 1)                         AS crar_pct_wtd
FROM fact_portfolio f
JOIN latest l ON f.quarter = l.quarter
GROUP BY f.layer
ORDER BY loan_book_lakh_cr DESC;


/* ==========================================================================
   SECTION 4 — SECTORAL ASSET-QUALITY HEATMAP FEED
   Q: Which loan segments carry the most stress, and are they improving?
   Technique: conditional aggregation to pivot quarters into columns
   (first vs latest) so a BI tool or reader can see the trajectory at a glance.
   ========================================================================== */
WITH bounds AS (
    SELECT MIN(period_end) AS first_pe, MAX(period_end) AS last_pe
    FROM fact_portfolio
)
SELECT
    f.sector,
    ROUND(SUM(CASE WHEN f.period_end = b.first_pe
              THEN f.gnpa_amount_cr END) * 100.0
          / NULLIF(SUM(CASE WHEN f.period_end = b.first_pe
              THEN f.loan_outstanding_cr END), 0), 2)              AS gnpa_pct_start,
    ROUND(SUM(CASE WHEN f.period_end = b.last_pe
              THEN f.gnpa_amount_cr END) * 100.0
          / NULLIF(SUM(CASE WHEN f.period_end = b.last_pe
              THEN f.loan_outstanding_cr END), 0), 2)              AS gnpa_pct_latest,
    ROUND(SUM(CASE WHEN f.period_end = b.last_pe
              THEN f.loan_outstanding_cr END) / 1e5, 2)            AS latest_book_lakh_cr
FROM fact_portfolio f
CROSS JOIN bounds b
GROUP BY f.sector
ORDER BY gnpa_pct_latest DESC;


/* ==========================================================================
   SECTION 5 — ENTITY RISK RANKING (latest quarter)
   Q: Which individual NBFCs look weakest right now on a blended risk view?
   Technique: NTILE to bucket entities into risk quintiles, RANK for ordering,
      and a composite score. Joins the fact table back to the entity master.
   ========================================================================== */
WITH latest AS (
    SELECT quarter FROM fact_portfolio ORDER BY period_end DESC LIMIT 1
),
entity_kpi AS (
    SELECT
        f.nbfc_id,
        d.nbfc_name,
        d.layer,
        SUM(f.loan_outstanding_cr)                                 AS book_cr,
        SUM(f.gnpa_amount_cr) * 100.0 / SUM(f.loan_outstanding_cr) AS gnpa_pct,
        SUM(f.nnpa_amount_cr) * 100.0 / SUM(f.loan_outstanding_cr) AS nnpa_pct,
        -- CRAR is a balance-sheet attribute: identical across an entity's rows,
        -- so MIN() just picks that single value safely.
        MIN(f.crar_pct)                                            AS crar_pct
    FROM fact_portfolio f
    JOIN latest l   ON f.quarter = l.quarter
    JOIN dim_nbfc d ON d.nbfc_id = f.nbfc_id
    GROUP BY f.nbfc_id, d.nbfc_name, d.layer
)
SELECT
    nbfc_name,
    layer,
    ROUND(book_cr / 1e3, 1)                                        AS book_k_cr,
    ROUND(gnpa_pct, 2)                                             AS gnpa_pct,
    ROUND(nnpa_pct, 2)                                             AS nnpa_pct,
    ROUND(crar_pct, 1)                                             AS crar_pct,
    NTILE(5) OVER (ORDER BY gnpa_pct DESC)                         AS gnpa_risk_quintile,
    RANK()   OVER (ORDER BY nnpa_pct DESC)                         AS nnpa_rank,
    -- Composite: higher NPA is worse, thinner CRAR is worse.
    ROUND(gnpa_pct + nnpa_pct + GREATEST(0, 15.0 - crar_pct), 2)  AS composite_risk_score
FROM entity_kpi
ORDER BY composite_risk_score DESC
LIMIT 15;
/* NOTE: SQLite added GREATEST()/LEAST() in 3.46 (2024). On older SQLite replace
   GREATEST(0, 15.0 - crar_pct) with MAX(0, 15.0 - crar_pct) — MAX/MIN act as the
   scalar greatest/least when given multiple arguments. Postgres/MySQL: keep GREATEST. */


/* ==========================================================================
   SECTION 6 — SLIPPAGE / FRESH-NPA FORMATION (cohort-style)
   Q: Which layer is generating the most *new* bad loans each quarter, relative
      to its standard book at the start of the quarter (the slippage ratio idea)?
   Technique: LAG to get prior-quarter standard assets per entity, aggregate to
      layer, then compute an annualised slippage ratio. This mirrors how RBI
      frames "slippage ratio = fresh accretion to NPAs / standard advances (opening)".
   ========================================================================== */
WITH entity_q AS (
    SELECT
        nbfc_id, layer, quarter, MIN(period_end) AS period_end,
        SUM(loan_outstanding_cr)                                   AS book_cr,
        SUM(gnpa_amount_cr)                                        AS gnpa_cr,
        SUM(loan_outstanding_cr) - SUM(gnpa_amount_cr)             AS standard_cr
    FROM fact_portfolio
    GROUP BY nbfc_id, layer, quarter
),
with_prior AS (
    SELECT
        *,
        LAG(gnpa_cr)     OVER (PARTITION BY nbfc_id ORDER BY period_end) AS prev_gnpa_cr,
        LAG(standard_cr) OVER (PARTITION BY nbfc_id ORDER BY period_end) AS prev_standard_cr
    FROM entity_q
)
SELECT
    layer,
    quarter,
    -- Fresh slippage = positive change in gross NPA rupees (accretions only).
    ROUND(SUM(MAX(0, gnpa_cr - prev_gnpa_cr)) / 1e3, 1)           AS fresh_slippage_k_cr,
    ROUND(SUM(prev_standard_cr) / 1e5, 2)                        AS opening_standard_lakh_cr,
    ROUND(400.0 * SUM(MAX(0, gnpa_cr - prev_gnpa_cr))
          / NULLIF(SUM(prev_standard_cr), 0), 2)                 AS annualised_slippage_pct
FROM with_prior
WHERE prev_gnpa_cr IS NOT NULL          -- drop the first quarter (no prior period)
GROUP BY layer, quarter
ORDER BY layer, MIN(period_end);
/* '400.0 *' annualises a quarterly ratio (x4) and converts to percent (x100). */


/* ==========================================================================
   SECTION 7 — PORTFOLIO CONCENTRATION RISK (HHI) IN PURE SQL
   Q: How concentrated is each NBFC's book across loan segments? A book that is
      all one segment is riskier than a diversified one.
   Technique: Herfindahl-Hirschman Index = sum of squared segment shares.
      Computed entirely in SQL with a share sub-aggregate, then squared and summed.
   ========================================================================== */
WITH latest AS (
    SELECT quarter FROM fact_portfolio ORDER BY period_end DESC LIMIT 1
),
shares AS (
    SELECT
        f.nbfc_id,
        f.sector,
        f.loan_outstanding_cr
          / SUM(f.loan_outstanding_cr) OVER (PARTITION BY f.nbfc_id) AS seg_share
    FROM fact_portfolio f
    JOIN latest l ON f.quarter = l.quarter
)
SELECT
    d.nbfc_name,
    d.layer,
    ROUND(SUM(s.seg_share * s.seg_share), 3)                       AS hhi,
    CASE
        WHEN SUM(s.seg_share * s.seg_share) >= 0.25 THEN 'High concentration'
        WHEN SUM(s.seg_share * s.seg_share) >= 0.15 THEN 'Moderate'
        ELSE 'Diversified'
    END                                                           AS concentration_band
FROM shares s
JOIN dim_nbfc d ON d.nbfc_id = s.nbfc_id
GROUP BY d.nbfc_name, d.layer
ORDER BY hhi DESC
LIMIT 15;
/* HHI ranges from 1/n (perfectly diversified across n segments) to 1 (single-segment).
   With 8 segments, the diversified floor is 0.125. */


/* ==========================================================================
   SECTION 8 — 2-QUARTER MOVING AVERAGE OF SECTOR GNPA (smoothing)
   Q: Strip out quarter-to-quarter noise to see the underlying asset-quality trend.
   Technique: AVG() over a ROWS BETWEEN frame — a windowed moving average.
   ========================================================================== */
WITH sector_q AS (
    SELECT
        quarter, MIN(period_end) AS period_end,
        SUM(gnpa_amount_cr) * 100.0 / SUM(loan_outstanding_cr) AS gnpa_pct
    FROM fact_portfolio
    GROUP BY quarter
)
SELECT
    quarter,
    ROUND(gnpa_pct, 2)                                            AS gnpa_pct,
    ROUND(AVG(gnpa_pct) OVER (
            ORDER BY period_end
            ROWS BETWEEN 1 PRECEDING AND CURRENT ROW), 2)        AS gnpa_2q_moving_avg
FROM sector_q
ORDER BY period_end;


/* ==========================================================================
   SECTION 9 — DATA-QUALITY ASSERTIONS
   Run these after loading. Every query should return ZERO rows; a non-empty
   result flags a load or integrity problem before it reaches the dashboard.
   ========================================================================== */

-- 9a. No orphan facts (every fact row must have a matching entity).
SELECT 'orphan_fact_rows' AS check_name, COUNT(*) AS bad_rows
FROM fact_portfolio f
LEFT JOIN dim_nbfc d ON d.nbfc_id = f.nbfc_id
WHERE d.nbfc_id IS NULL
HAVING COUNT(*) > 0;

-- 9b. Net NPA can never exceed Gross NPA (a logical impossibility if it does).
SELECT 'nnpa_gt_gnpa' AS check_name, COUNT(*) AS bad_rows
FROM fact_portfolio
WHERE nnpa_amount_cr > gnpa_amount_cr + 0.01
HAVING COUNT(*) > 0;

-- 9c. Ratios must be within sane bounds.
SELECT 'ratio_out_of_bounds' AS check_name, COUNT(*) AS bad_rows
FROM fact_portfolio
WHERE gnpa_pct < 0 OR gnpa_pct > 100
   OR crar_pct < 0 OR crar_pct > 100
   OR pcr < 0 OR pcr > 1
HAVING COUNT(*) > 0;

-- 9d. Every entity should appear in every quarter (balanced panel).
SELECT 'unbalanced_panel' AS check_name,
       COUNT(*) AS entities_with_missing_quarters
FROM (
    SELECT nbfc_id, COUNT(DISTINCT quarter) AS q
    FROM fact_portfolio
    GROUP BY nbfc_id
    HAVING q < (SELECT COUNT(DISTINCT quarter) FROM fact_portfolio)
)
HAVING COUNT(*) > 0;

/* End of nbfc_analytics.sql */
