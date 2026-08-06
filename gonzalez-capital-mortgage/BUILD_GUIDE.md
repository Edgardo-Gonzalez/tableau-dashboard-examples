# Tableau Build Guide — Gonzalez Capital Mortgage

This guide builds the portfolio dashboard from
`gonzalez_capital_mortgage.db`. Connect Tableau to the SQLite file in the
project root; it already contains indexed tables and seven analytical views.

## Workbook standards

- **Font:** Aptos or Tableau Book.
- **Core palette:** navy `#062B5B`, gold `#B8892D`, charcoal `#3D3D3D`, white
  `#FFFFFF`, and light canvas `#F6F7F9`.
- **Status colors:** Post-Shock navy; Pre-Shock gold; Rural gold; Metro blue-gray
  `#7E97AE`; adverse / fallout `#B84A4A`.
- **Brand assets:** use `assets/logos/logo-horizontal-lockup.png` in dashboard
  headers, `app-icon-gcm.png` as a compact navigation mark, and `watermark.png`
  at 6–10% opacity behind spacious dashboard canvases.
- **Data-quality rule:** exclude `[Has Timestamp Anomaly] = 1` from duration
  charts. Keep it available as a visible data-quality callout rather than
  silently changing the data.

## Dashboard 1 — Where Did the Time Go?

**Audience:** executive operations leadership.

**Message:** the rate-driven surge exposed an appraisal-capacity and condition-
rework problem, not an underwriting staffing problem.

**Canvas:** fixed 1,440 × 900 px. Use a white canvas, a 72 px navy header, and
16 px gutters. Place `logo-horizontal-lockup.png` at the header’s left edge.

### Data sources

Add these SQLite objects independently:

1. `fact_loan` for KPI cards and duration detail.
2. `vw_appraisal_delay_by_county` for the map.
3. `vw_condition_rework` for condition analysis.
4. `vw_funnel_by_month` for fallout dollars.

### Workbook fields

Create the following calculated fields against `fact_loan`.

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
// Rural Delay Flag
IF [Market Tier] = "Rural" THEN "Rural" ELSE "Metro" END
```

Create a string parameter named **Period Focus**, with values `All`,
`Pre-Shock`, and `Post-Shock`, then this filter:

```tableau
// Period Focus Filter
[Period Focus] = "All" OR [Shock Period] = [Period Focus]
```

Apply the filter to every worksheet that uses `fact_loan`.

### Sheet 1 — Executive KPI strip

Create four text sheets using `fact_loan`, each filtered to `[Period Focus
Filter]` and, where applicable, `[Valid Duration Loan]`.

| KPI | Mark | Field | Format |
| --- | --- | --- | --- |
| Median cycle time | Text | `MEDIAN([Days Total Cycle])` | `0.0 "days"` |
| Appraisal wait | Text | `MEDIAN([Days Appraisal Wait])` | `0.0 "days"` |
| Fallout dollars | Text | `SUM([Fallout Dollars])` | `$#,##0,,"M"` |
| Rural share of delay | Text | `SUM(IF [Market Tier] = "Rural" THEN [Days Appraisal Wait] END) / SUM([Days Appraisal Wait])` | `0%` |

Use an 11 pt uppercase label above each 28 pt value. Add a subtitle under the
strip: **“Rates fell, applications surged, and the bottleneck moved outside
underwriting.”**

### Sheet 2 — Stage decomposition

Use this Custom SQL as a new Tableau data source. It turns stage columns into a
single `[Stage]` dimension and `[Days]` measure.

```sql
SELECT shock_period, market_tier, property_state, channel, region, loan_id,
       'Document collection' AS stage, days_app_to_docs AS days
FROM fact_loan
UNION ALL SELECT shock_period, market_tier, property_state, channel, region, loan_id,
       'Processing', days_processing FROM fact_loan
UNION ALL SELECT shock_period, market_tier, property_state, channel, region, loan_id,
       'Appraisal wait', days_appraisal_wait FROM fact_loan
UNION ALL SELECT shock_period, market_tier, property_state, channel, region, loan_id,
       'UW queue', days_uw_queue_wait FROM fact_loan
UNION ALL SELECT shock_period, market_tier, property_state, channel, region, loan_id,
       'UW touch', days_uw_touch FROM fact_loan
UNION ALL SELECT shock_period, market_tier, property_state, channel, region, loan_id,
       'Condition clearing', days_condition_clearing FROM fact_loan
UNION ALL SELECT shock_period, market_tier, property_state, channel, region, loan_id,
       'CTC to funding', days_ctc_to_funding FROM fact_loan;
```

Filter `days >= 0`, keep only `Pre-Shock` and `Post-Shock`, and use side-by-side
bars: `[Stage]` on Rows, `AVG([Days])` on Columns, and `[Shock Period]` on
Color. Sort stages in the SQL order. Add a gold annotation on **Appraisal wait**:
“External capacity, not underwriting, absorbed the surge.”

### Sheet 3 — Wait versus touch

Use `vw_stage_cycle_time`. Put `shock_period` on Columns and Measure Values on
Rows. Retain only `avg_appraisal_wait`, `avg_uw_wait`, `avg_uw_touch`, and
`avg_condition_days`; put Measure Names on Color. Filter to Pre- and Post-Shock.
Use grouped bars, with waits in navy/gold and touch time in muted gray.

Title: **“Underwriting touch time rose modestly; waiting did not.”**

### Sheet 4 — Appraisal-delay map

Use `vw_appraisal_delay_by_county`. Set the geographic role of
`property_county` to County and `property_state` to State. Use a filled map with
`AVG(avg_wait)` on Color and `SUM(loans)` on Detail. Filter to `Post-Shock`.

Tooltip:

```text
<property_county>, <property_state>
Average appraisal wait: <AVG(avg_wait)> days
Panel depth: <AVG(appraiser_panel_depth)> appraisers
Excess delay vs. baseline: <SUM(excess_over_baseline)> days
Loans affected: <SUM(loans)>
```

Use a sequential light-to-gold color ramp. Add a dashboard filter action from
this map to the stage and condition sheets, filtering by State.

### Sheet 5 — Condition rework trend

Use `vw_condition_rework`. Put `condition_category` on Rows and
`AVG(avg_round)` on Columns. Color by `responsible_party`; filter to
Post-Shock. Add `AVG(avg_days_to_clear)` to Tooltip.

Title: **“Rework adds borrower-response days after approval.”**

### Sheet 6 — Fallout dollars callout

Use `vw_funnel_by_month`. Put `app_month` on Columns and `SUM(lost_volume)` on
Rows; filter `app_month` to the last 24 months. Use a thin gold line, add a
reference band for the During-Shock period, and display the post-shock total as
a large red annotation.

### Dashboard assembly

Arrange the elements in this order:

1. Navy header: logo, title **“Where Did the Time Go?”**, and Period Focus
   parameter control.
2. KPI strip.
3. Stage decomposition (left, 60%) and wait-versus-touch comparison (right,
   40%).
4. Appraisal-delay map (left, 60%) and condition-rework chart (right, 40%).
5. Fallout-dollar callout and a three-item recommendation box.

Recommendation copy:

1. **Expand appraisal panels** in rural markets with thin vendor coverage.
2. **Front-load borrower documents** to reduce condition rounds.
3. **Do not mass-hire underwriters**; the queue is not the primary constraint.

## Dashboards 2 and 3

Build these after Dashboard 1 is screenshot-ready:

- **Post-Closing Operations:** use `vw_post_closing_aging` for trailing-doc
  aging, suspense reasons, exception rate by branch, funding mix, and investor
  delivery timeliness.
- **Servicing & MSR:** use `vw_msr_portfolio_monthly`,
  `vw_msr_vintage_performance`, and `fact_msr_rate_shock` for portfolio value,
  roll-forward, CPR sensitivity, vintage performance, and a rate-shock
  parameter.

Keep the same header, logo treatment, filters, and gold action color across all
three tabs so the portfolio reads as one application.
