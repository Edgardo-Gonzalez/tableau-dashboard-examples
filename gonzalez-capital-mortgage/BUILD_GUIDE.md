# Tableau Build Guide — Gonzalez Capital Mortgage

Build this Tableau portfolio workbook exclusively from the CSVs in `data/`.
Using Tableau’s **Text file** connector keeps the workbook portable and avoids
requiring a database driver.

## Workbook standards

- **Font:** Aptos or Tableau Book.
- **Core palette:** Institutional Navy `#062B59`, Capital Gold `#B8892E`,
  Graphite `#3E3E3E`, Silver Gray `#8A9098`, and white `#FFFFFF`.
- **Brand assets:** place `assets/logos/logo-horizontal-lockup.png` in each
  dashboard header. Use `app-icon-gcm.png` for small navigation marks and
  `watermark.png` at 6–10% opacity behind spacious dashboard canvases.
- **Data quality:** exclude `[Has Timestamp Anomaly] = 1` from duration charts;
  retain it as a visible data-quality callout.

## Dashboard 1 — Where Did the Time Go?

**Audience:** executive operations leadership.

**Message:** the rate-driven surge exposed an appraisal-capacity and
condition-rework problem, not an underwriting staffing problem.

**Canvas:** fixed 1,440 × 900 px. Use a white canvas, a 72 px navy header, and
16 px gutters.

### Data model

Add `data/fact_loan.csv` as the primary source. It powers the KPI strip, stage
analysis, map, and fallout trend without joins.

For the condition worksheet, add `data/fact_condition.csv` and relate it to
`fact_loan.csv` on `loan_id`, with conditions on the many side. No physical join
is needed.

### Calculated fields

Create these fields against `fact_loan.csv`.

```tableau
// Completed Loan
[Status] = "Funded"
```

```tableau
// Valid Duration Loan
[Completed Loan] AND [Has Timestamp Anomaly] = 0
```

```tableau
// Fallout Dollars
IF [Status] = "Fallout" THEN [Loan Amount] END
```

```tableau
// Application Month
DATETRUNC('month', [Application Date])
```

Create a string parameter named **Period Focus**, with values `All`,
`Pre-Shock`, and `Post-Shock`, then apply this filter to all loan-based sheets.

```tableau
// Period Focus Filter
[Period Focus] = "All" OR [Shock Period] = [Period Focus]
```

### Sheet 1 — Executive KPI strip

Create four text worksheets, filtered by `[Period Focus Filter]` and—except
for fallout—`[Valid Duration Loan]`.

| KPI | Field | Format |
| --- | --- | --- |
| Median cycle time | `MEDIAN([Days Total Cycle])` | `0.0 "days"` |
| Appraisal wait | `MEDIAN([Days Appraisal Wait])` | `0.0 "days"` |
| Fallout dollars | `SUM([Fallout Dollars])` | `$#,##0,,"M"` |
| Rural delay share | `SUM(IF [Market Tier] = "Rural" THEN [Days Appraisal Wait] END) / SUM([Days Appraisal Wait])` | `0%` |

Use an 11 pt uppercase label above each 28 pt value. Subtitle:
**“Rates fell, applications surged, and the bottleneck moved outside
underwriting.”**

### Sheet 2 — Stage decomposition

Use `fact_loan.csv`; no custom SQL or pivot is required. Put **Measure Names**
on Rows, **Measure Values** on Columns, and `[Shock Period]` on Color. Retain
only these measures in Measure Values:

1. `AVG([Days App to Docs])`
2. `AVG([Days Processing])`
3. `AVG([Days Appraisal Wait])`
4. `AVG([Days UW Queue Wait])`
5. `AVG([Days UW Touch])`
6. `AVG([Days Condition Clearing])`
7. `AVG([Days CTC to Funding])`

Filter to completed, valid-duration loans and Pre-/Post-Shock. Alias the
Measure Names to concise stage labels, preserve the order above, and use
side-by-side bars. Add a gold annotation to appraisal wait: **“External
capacity—not underwriting—absorbed the surge.”**

### Sheet 3 — Wait versus touch

Put `[Shock Period]` on Columns and Measure Values on Rows. Retain
`AVG([Days Appraisal Wait])`, `AVG([Days UW Queue Wait])`,
`AVG([Days UW Touch])`, and `AVG([Days Condition Clearing])`; place Measure
Names on Color. Filter to completed, valid-duration loans and Pre-/Post-Shock.

Use grouped bars, with waits in navy/gold and touch time in muted gray. Title:
**“Underwriting touch time rose modestly; waiting did not.”**

### Sheet 4 — Appraisal-delay map

Set geographic roles: `[Property County]` = County and `[Property State]` =
State. Use a filled map with `AVG([Days Appraisal Wait])` on Color and
`COUNTD([Loan ID])` on Detail. Filter to completed, valid-duration loans in
Post-Shock.

Tooltip:

```text
<Property County>, <Property State>
Average appraisal wait: <AVG(Days Appraisal Wait)> days
Panel depth: <AVG(Appraiser Panel Depth)> appraisers
Loans affected: <COUNTD(Loan ID)>
```

Use a sequential light-to-gold color ramp. Add a dashboard filter action from
the map to the stage and condition sheets by State.

### Sheet 5 — Condition rework

Use the relationship between `fact_condition.csv` and `fact_loan.csv`. Put
`[Condition Category]` on Rows and `AVG([Round Number])` on Columns. Color by
`[Responsible Party]`; filter the related loan records to Post-Shock. Include
`AVG([Days to Clear])` and `COUNTD([Loan ID])` in the tooltip.

Title: **“Rework adds borrower-response days after approval.”**

### Sheet 6 — Fallout dollars

Put `[Application Month]` on Columns and `SUM([Fallout Dollars])` on Rows;
filter to the last 24 months. Use a thin gold line, add a reference band for
the During-Shock period, and show the post-shock total as a large red callout.

### Dashboard assembly

1. Navy header: logo, title **“Where Did the Time Go?”**, and Period Focus.
2. KPI strip.
3. Stage decomposition (60% width) and wait-versus-touch chart (40%).
4. Appraisal-delay map (60%) and condition-rework chart (40%).
5. Fallout-dollar callout and these recommendations:
   - Expand appraisal panels in rural markets with thin vendor coverage.
   - Front-load borrower documents to reduce condition rounds.
   - Do not mass-hire underwriters; the queue is not the primary constraint.

## Dashboards 2 and 3

Build these after Dashboard 1 is screenshot-ready:

- **Post-Closing Operations:** use `fact_loan.csv` for trailing-document aging,
  suspense reasons, exception rate by branch, funding mix, and investor-delivery
  timeliness.
- **Servicing & MSR:** use `fact_msr_monthly.csv` and
  `fact_msr_rate_shock.csv` for portfolio value, roll-forward, CPR sensitivity,
  vintage performance, and a rate-shock parameter.

Keep the same header, logo treatment, filters, and gold action color across all
three tabs so the portfolio reads as one cohesive application.
