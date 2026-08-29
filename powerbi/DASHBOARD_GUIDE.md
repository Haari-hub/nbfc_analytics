# Power BI Dashboard — Build Guide

This guide reconstructs the **NBFC Portfolio & Asset-Quality** dashboard from the
three CSVs in `../data`. Follow it top-to-bottom and you will have a 3-page report
in roughly 30–40 minutes. The DAX for every measure referenced here is in
`measures.dax` (copy-paste ready). A visual reference of the target layout is in
`dashboard_mockup.png`.

> **Why a build guide and not a `.pbix`?** A `.pbix` is a compiled binary written
> by Power BI Desktop; it can't be authored outside the tool and is brittle across
> versions. Shipping the data model spec + all DAX + exact visual configs is both
> reproducible on any Power BI version and, frankly, the artefact that shows you
> understand the modelling — which is what a reviewer is looking for.

---

## 1. Load the data

`Home → Get data → Text/CSV` and import all three files from `../data`:

| File | Role in model | Notes |
|---|---|---|
| `dim_nbfc.csv` | Dimension (entity master) | one row per NBFC |
| `fact_portfolio.csv` | **Fact** (NBFC × quarter × sector) | the grain everything measures |
| `agg_sector_quarter.csv` | Validation / backup | load, but leave **unrelated** |

These CSVs are produced by the ETL script (`../python/etl_nbfc.py`), so they load
clean. In Power Query, just confirm the types before `Close & Apply`:

- `loan_outstanding_cr`, `gnpa_amount_cr`, `nnpa_amount_cr`, all `*_pct`, `pcr` → **Decimal Number**
- `period_end` → **Date**
- everything else (`nbfc_id`, `nbfc_name`, `layer`, `region`, `quarter`, `sector`) → **Text**

---

## 2. Build the date table

QoQ time-intelligence needs a proper Date table. In `Modeling → New Table`:

```DAX
dim_date =
VAR MinD = MIN ( fact_portfolio[period_end] )
VAR MaxD = MAX ( fact_portfolio[period_end] )
RETURN
ADDCOLUMNS (
    CALENDAR ( DATE ( YEAR(MinD), 1, 1 ), DATE ( YEAR(MaxD), 12, 31 ) ),
    "Year", YEAR ( [Date] ),
    "Quarter No", QUARTER ( [Date] ),
    "FY", "FY" & IF ( MONTH([Date]) >= 4, YEAR([Date]) + 1, YEAR([Date]) ),
    "period_end", [Date]
)
```

Then `Table tools → Mark as date table → Date`. Rename the column used for the
relationship to `period_end` (already done above) so it matches the fact table.

> The fact quarters are period-*end* dates (31-Mar, 30-Sep), so the join is on the
> exact `period_end` value, not on a generated day. If you prefer, relate on a
> `quarter` text key instead and drop the DATEADD measures for simple LAG visuals.

---

## 3. Create relationships

`Model view` — drag to create:

```
dim_nbfc[nbfc_id]   1 ──────< *  fact_portfolio[nbfc_id]     (single direction)
dim_date[period_end] 1 ─────< *  fact_portfolio[period_end]  (single direction)
```

Leave `agg_sector_quarter` **disconnected**. Both relationships are single-direction,
1-to-many, fact on the many side. That is the whole star schema.

---

## 4. Add the measures

Create a blank table `_Measures` (`Enter data → name it → Load`), then paste each
measure from `measures.dax`. Group them into display folders in the Fields pane:

- **Base**: Loan Book (Cr), Gross NPA (Cr), Net NPA (Cr), Loan Book (Lakh Cr)
- **Ratios**: GNPA %, NNPA %, Provision Coverage %, CRAR % (wtd), RoA % (wtd)
- **Time**: Credit Growth % QoQ, GNPA % QoQ Change (pp), Fresh Slippage (Cr)
- **Risk**: NBFC GNPA Rank, Composite Risk Score, Portfolio HHI
- **Status**: GNPA Status, CRAR Below Reg Min Flag, Selected Quarter Title

---

## 5. Page 1 — Sector Executive Summary

**Purpose:** the one-screen "state of the NBFC sector" a credit committee would open with.

**Slicers (top strip):** `Quarter` (dropdown), `Layer` (buttons), `Region` (dropdown).

**KPI cards (row of five):**

| Card | Measure | Formatting |
|---|---|---|
| Loan Book | `Loan Book (Lakh Cr)` | 1 dp, suffix " L Cr" |
| GNPA % | `GNPA %` | 2 dp, conditional colour via `GNPA Status` |
| NNPA % | `NNPA %` | 2 dp |
| Provision Coverage | `Provision Coverage %` | 0 dp |
| CRAR | `CRAR % (wtd)` | 1 dp; data label uses `CRAR Below Reg Min Flag` |

**Main visuals:**

1. **Line chart — asset-quality trend.** Axis `dim_date[FY]` + quarter; lines
   `GNPA %`, `NNPA %`. Add a constant line at **6%** (analytics pane) labelled
   "High-stress threshold". This is the hero visual: it shows the sector's GNPA
   falling from ~6.4% to ~3.1% across the window.
2. **Clustered column — sector heatmap feed.** Axis `sector`; value `GNPA %`;
   sort descending. Colour bars by `GNPA Status`. Microfinance and unsecured
   personal sit at the top; gold and housing at the bottom.
3. **Line + column combo — credit growth vs GNPA.** Column `Credit Growth % QoQ`,
   line `GNPA %`, shared quarter axis. Tells the "growing fast *and* cleaning up"
   story in one visual.

---

## 6. Page 2 — Layer & Segment Deep-Dive (Scale-Based Regulation view)

**Purpose:** compare the Upper / Middle / Base layers RBI's SBR framework defines.

**Slicers:** `Quarter`, `Sector`.

**Visuals:**

1. **Matrix — the league table.** Rows `layer`; values `Loan Book (Lakh Cr)`,
   `GNPA %`, `NNPA %`, `CRAR % (wtd)`, `RoA % (wtd)`. Turn on data bars on `GNPA %`.
   Middle Layer should read ~64–65% of the book (matches RBI's published share).
2. **100% stacked column — book mix by layer over time.** Axis quarter; legend
   `layer`; value `Loan Book (Cr)`. Shows how the composition shifts.
3. **Ribbon/stacked column — sector exposure within selected layer.** Axis quarter;
   legend `sector`; value `Loan Book (Cr)`. Cross-filters from the matrix.
4. **Small-multiple line — slippage by layer.** Axis quarter; value
   `Fresh Slippage (Cr)`; small multiple by `layer`. Surfaces which layer forms
   fresh NPAs fastest.

---

## 7. Page 3 — Entity Risk Drilldown

**Purpose:** rank individual NBFCs and inspect one at a time — the "who's weakest" page.

**Slicers:** `Quarter`, `Layer`, and a numeric **Top-N** parameter on GNPA (optional).

**Visuals:**

1. **Table — entity risk ranking.** Columns `nbfc_name`, `layer`, `Loan Book (Cr)`,
   `GNPA %`, `NNPA %`, `CRAR % (wtd)`, `Composite Risk Score`, `NBFC GNPA Rank`.
   Sort by `Composite Risk Score` desc. Conditional format `CRAR % (wtd)` red below 15.
2. **Scatter — risk vs size.** X `Loan Book (Cr)`, Y `GNPA %`, size `Net NPA (Cr)`,
   legend `layer`, details `nbfc_name`. Big high-GNPA bubbles are the ones to watch.
3. **Gauge / card — single-entity HHI.** `Portfolio HHI` (shows only when one NBFC
   is selected, thanks to `HASONEVALUE`). Add a text box explaining 0.125 = diversified.
4. **Decomposition tree (optional, great in interviews):** analyse `Gross NPA (Cr)`
   by `layer → sector → nbfc_name`. Lets you click down to the biggest stress pockets.

---

## 8. Formatting pass (do this last — it's what makes it look senior)

- **One theme.** `View → Themes → Customize`: a restrained palette (one accent
  colour for the metric in focus, grey for context). Avoid the default rainbow.
- **Titles are sentences, not field names.** "Sector GNPA has more than halved
  since FY21", not "GNPA % by Quarter".
- **Right-size numbers.** Lakh-crore on headline cards, % to 1–2 dp, no 6-decimal noise.
- **Consistent slicer strip** in the same position on every page.
- **Tooltips.** Add a tooltip page showing GNPA %, NNPA %, PCR, CRAR so hovering any
  bar gives the full KPI set.
- **Alt text + tab order** for accessibility (Selection pane) — reviewers notice.

---

## 9. Sanity checks before you publish

Cross-check the dashboard against the SQL and the RBI anchors:

- Page-1 sector `GNPA %` for **FY25Q4 ≈ 3.1%** and **FY21Q4 ≈ 6.4%** — these match
  the RBI control totals in `../data/rbi_sector_controls.csv`.
- Page-2 **Middle Layer share ≈ 64–65%** of the book.
- Run `../sql/nbfc_analytics.sql` Section 1 and confirm the numbers tie out to the
  KPI cards. If they don't, a Power Query type or relationship is wrong.

Once it ties out, `File → Publish` to the Power BI Service (free account) and put the
public link (or a screenshot) in the repo README.
