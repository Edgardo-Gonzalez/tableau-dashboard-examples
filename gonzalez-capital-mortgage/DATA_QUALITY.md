# Data Quality Artifacts

The dataset contains deliberate imperfections so it behaves like a real
extract rather than a textbook example. Each one has a plausible operational
cause, is reproducible, and is documented here.

This file exists so the anomalies are **discussable rather than embarrassing**.
Finding them and handling them explicitly is part of the work the dashboard
demonstrates.

---

## 1. Non-random missing values

**What:** ~2% of values missing overall, but concentrated by channel:

| Channel | DTI null | CLTV null | Employment null |
|---|---:|---:|---:|
| Retail | 1.1% | 1.7% | 1.2% |
| Correspondent | 2.6% | 3.4% | 3.4% |
| **Wholesale** | **5.7%** | **8.4%** | **6.5%** |

**Cause:** Broker-submitted files pass through a third-party LOS and lose
fields in translation. Retail files are keyed directly into the system of
record.

**Why it matters:** This is *missing not at random*. A naive `WHERE dti IS NOT
NULL` silently drops Wholesale loans at 5× the rate of Retail, biasing any
channel comparison. Handle by either imputing, reporting coverage alongside the
metric, or explicitly scoping the analysis.

**Affected fields:** `dti`, `fico`, `cltv`, `employment_type`, `property_type`,
`occupancy`, `appraised_value`, `lock_extensions`,
`title_policy_received_date`, `final_docs_received_date`,
`investor_delivery_date`, `suspense_reason`

---

## 2. DTI unit inconsistency (units bug)

**What:** Branch `BR-502` (Wichita) recorded DTI as a decimal (`0.42`) instead
of a percentage (`42`) from **2025-03-01 through 2025-07-31**. 202 rows
affected.

**Cause:** A classic units bug — an LOS configuration change at one branch
that nobody caught for five months.

**Why it matters:** `AVG(dti)` across the portfolio is silently wrong. The bug
is invisible in an aggregate but obvious in a branch-level distribution.

**Detection:**
```sql
SELECT branch_id, MIN(dti), MAX(dti) FROM fact_loan
WHERE dti IS NOT NULL GROUP BY branch_id;
```

**Handling (Tableau calculated field):**
```
// DTI (Corrected)
IF [Dti] < 1 THEN [Dti] * 100 ELSE [Dti] END
```

---

## 3. Duplicate applications (reapplications)

**What:** 40 loans are near-duplicates of a previously denied application from
the same borrower — new `loan_id`, application date 20–75 days later, slightly
improved FICO and DTI. Flagged by `is_reapplication = 1`.

**Cause:** Borrower fixes the issue that caused the denial and reapplies. This
is normal business, not a data error — but it double-counts if you are
measuring unique borrowers or true application volume.

**Why it matters:** `SELECT DISTINCT loan_id` will not find these; they are
distinct loans. Detection requires fuzzy matching on borrower and property
attributes.

**Handling:** Depends on the question. For funnel conversion, count them
separately. For "how many borrowers did we serve," dedupe. The flag is
provided so you can do either.

---

## 4. Out-of-sequence timestamps

**What:** 55 loans have `underwriting_start` *before* the file was actually
ready for underwriting (before both `processing_end` and `appraisal_received`),
producing **negative** `days_uw_queue_wait`. Flagged by
`has_timestamp_anomaly = 1`.

**Cause:** Manual back-dating in the LOS during a correction, or a clock issue
in a batch migration.

**Why it matters:** Any unguarded duration calculation returns a negative
number, which then corrupts averages. Real pipelines have this, and defensive
duration math is the right response.

**Detection:**
```sql
SELECT COUNT(*) FROM fact_loan WHERE days_uw_queue_wait < 0;
```

**Handling (Tableau calculated field):**
```
// UW Queue Wait (Guarded)
IF [Days Uw Queue Wait] < 0 THEN NULL ELSE [Days Uw Queue Wait] END
```

---

## 5. Inconsistent string formatting

**What:** `property_type` and `occupancy` contain case and whitespace variants
of the same value — 15 distinct strings for 5 real values:

```
'Single Family'   'Single Family '   'SINGLE FAMILY'
'Condo'           'Condo '           'CONDO'
...
```

**Cause:** The same field written by multiple upstream systems with different
normalization rules.

**Why it matters:** A naive `GROUP BY property_type` produces 15 rows instead
of 5, fragmenting every category.

**Handling (Tableau calculated field):**
```
// Property Type (Clean)
PROPER(TRIM([Property Type]))
```

---

## 6. AMC vendor outage

**What:** Appraisal turnaround for vendor `AMC-02` (Cornerstone Appraisal
Group) spikes for three weeks starting **2025-02-10**:

| Month | AMC-02 avg wait |
|---|---:|
| Jan 2025 | 20.0d |
| **Feb 2025** | **29.0d** |
| Mar 2025 | 21.3d |

Other vendors are stable across the same window.

**Cause:** A vendor-side system outage. Orders routed to AMC-02 stalled.

**Why it matters:** This is a *real anomaly with a real explanation*, not
noise. It is a good example of why you segment by vendor before concluding
anything about appraisal performance — and it is distinct from the structural
rural-panel problem that drives the main narrative.

---

## 7. Weekend, holiday, and month-end effects

**What:** Applications collapse on weekends (~20% of a weekday) and federal
holidays (~18%). Closings and fundings are pushed to the next business day.
Funding volume clusters in the last five business days of each month.

**Cause:** Normal business rhythm.

**Why it matters:** Any daily time series will look noisy and cyclical.
Day-of-week and business-day flags are provided in `dim_date`. Weekly or
monthly aggregation is usually the right altitude.

---

## What is *not* messy

`fact_msr_monthly` is deliberately clean. Servicing data comes from a separate
system of record with tighter controls than the origination LOS — which is
itself a realistic detail worth mentioning: data quality is usually a property
of the *source system*, not the company.
