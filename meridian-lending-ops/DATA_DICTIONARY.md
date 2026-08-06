# Data Dictionary

12 tables. Star schema: `fact_loan` is the primary fact; three supporting facts
at different grains; six dimensions.

**Join keys:** `loan_id`, `branch_id`, `geo_id`, `employee_id`, `vendor_id`,
date columns → `dim_date.date_key`.

---

## fact_loan — 22,037 rows

Grain: **one row per loan application.** The primary fact table.

### Identity & status

| Column | Type | Notes |
|---|---|---|
| `loan_id` | text | PK. Format `ML-######` |
| `application_date` | date | Application submitted |
| `status` | text | `Funded` / `Fallout` / `In Process` |
| `shock_period` | text | `Pre-Shock` (≤2024-07-31), `During Shock`, `Post-Shock` (≥2024-10-01). Precomputed so the dashboard need not hardcode dates |

### Routing

| Column | Type | Notes |
|---|---|---|
| `channel` | text | `Retail` / `Wholesale` / `Correspondent` |
| `branch_id`, `branch_name`, `region`, `branch_size` | text | Denormalized from `dim_branch` |
| `loan_officer_id`, `processor_id`, `underwriter_id`, `closer_id` | text | → `dim_employee.employee_id` |
| `amc_vendor_id` | text | → `dim_vendor.vendor_id`. Appraisal management company |

### Property & geography

| Column | Type | Notes |
|---|---|---|
| `geo_id` | text | → `dim_geography.geo_id` |
| `property_state`, `property_county`, `property_msa` | text | |
| `market_tier` | text | `Metro` / `Rural` — **key to the geographic finding** |
| `rural_flag` | int | 0/1 |
| `appraiser_panel_depth` | int | Active appraisers covering that county. Drives appraisal congestion |
| `property_type` | text | ⚠️ Contains case/whitespace variants — see DATA_QUALITY.md |
| `occupancy` | text | ⚠️ Same |

### Loan characteristics

| Column | Type | Notes |
|---|---|---|
| `loan_purpose` | text | `Purchase` / `Rate-Term Refi` / `Cash-Out Refi`. Mix shifts toward refi as rates fall |
| `loan_type` | text | `Conventional` / `FHA` / `VA` / `USDA` / `Jumbo` |
| `loan_amount`, `appraised_value` | real | USD. Respects product limits — see below |
| `ltv`, `cltv` | real | Percent. `cltv ≥ ltv` always |
| `dti` | real | Percent. ⚠️ BR-502 has a units bug — see DATA_QUALITY.md |
| `fico` | int | 580–840. Correlated with DTI, LTV, loan type |
| `employment_type` | text | `W2` / `Self-Employed` / `Retired` / `Mixed`. Self-employed drives longer doc collection and more conditions |
| `note_rate` | real | Market rate + risk-based adjustments |
| `lock_date`, `lock_term_days`, `lock_expiration` | date/int | |
| `lock_extensions` | int | Derived from cycle-time overrun — the direct cost of the delay |

#### Product eligibility rules (enforced)

Every loan satisfies the constraints a real origination system would enforce.
`validate.py` asserts all of these, so they cannot silently regress.

| Rule | Enforcement |
|---|---|
| Conforming limit | Conventional ≤ $806,500; Jumbo > $806,500 |
| FHA ceiling | ≤ $524,225 |
| USDA practical limit | ≤ $377,600, and **no cash-out** (program does not permit it) |
| Max LTV — purchase | Conv 97%, FHA 96.5%, VA/USDA 100%, **Jumbo 89.9%** |
| Max LTV — cash-out | Conv/FHA 80%, VA 90%, Jumbo 75% |
| Occupancy overlay | Investment ≤ 85% LTV, Second Home ≤ 90% |
| Government occupancy | FHA/VA/USDA are **primary-residence only** |
| Minimum FICO | Conv 620, Jumbo 700, USDA 640, FHA/VA 580 |
| Government pricing | VA/FHA/USDA price *below* conventional at equal credit (the guaranty) |

Resulting profile:

| Product | n | Avg amount | Max LTV | Min FICO | Avg rate |
|---|---:|---:|---:|---:|---:|
| Jumbo | 2,178 | $1,017,622 | 89.9% | 700 | 6.65% |
| VA | 2,376 | $335,459 | 100% | 592 | 6.35% |
| Conventional | 13,718 | $331,221 | 96.5% | 620 | 6.63% |
| FHA | 2,972 | $321,489 | 96.5% | 580 | 6.60% |
| USDA | 793 | $236,186 | 96.5% | 640 | 6.41% |

### Stage timestamps

Ordered through the pipeline. `NULL` beyond the stage where a loan fell out.

| Column | Notes |
|---|---|
| `docs_received` | Borrower documentation complete |
| `processing_start`, `processing_end` | |
| `appraisal_ordered`, `appraisal_received` | |
| `uw_ready_date` | **`MAX(processing_end, appraisal_received)`** — the true gate. Underwriting cannot begin until both are done |
| `underwriting_start`, `underwriting_end` | |
| `conditional_approval_date` | Same as `underwriting_end` |
| `condition_rounds` | Int. Iterations of the condition loop — **the rework metric** |
| `final_conditions_cleared`, `ctc_date`, `closing_scheduled`, `funded_date` | |

### Derived durations (days)

| Column | Definition |
|---|---|
| `days_app_to_docs` | application → docs received |
| `days_processing` | processing touch time |
| `days_appraisal_wait` | ordered → received. **The bottleneck** |
| `days_uw_queue_wait` | `uw_ready_date` → `underwriting_start`. ⚠️ Negative on 55 anomaly rows |
| `days_uw_touch` | Active underwriting time |
| `days_condition_clearing` | Conditional approval → all conditions cleared |
| `days_ctc_to_funding` | |
| `days_total_cycle` | application → funded |

> **Why `days_uw_queue_wait` is measured from `uw_ready_date`:** measuring from
> `processing_end` alone would attribute appraisal delay to underwriting — the
> exact misdiagnosis this dashboard exists to correct.

### Fallout

| Column | Notes |
|---|---|
| `fallout_reason` | 8 values. Withdrawals vs. denials |
| `fallout_stage` | Stage where the loan died |

### Post-closing

| Column | Notes |
|---|---|
| `funding_type` | `Wet` / `Dry`. Dry-funding states: NM, WY, MT |
| `title_policy_received_date`, `recording_date`, `final_docs_received_date` | Rural counties run slower |
| `investor_delivery_date`, `purchase_advice_date` | |
| `trailing_docs_status` | `Complete` / `Pending Title Policy` / `Pending Final Docs` / `Pending MI Certificate` / `Exception` |
| `trailing_docs_age_days` | Aging on open items as of 2025-12-31 |
| `suspense_flag`, `suspense_reason`, `days_in_suspense` | Investor rejected pending a fix |
| `first_payment_date`, `escrow_flag` | |
| `servicing_status` | `Retained` / `Released` / `Sold`. Only `Retained` appears in `fact_msr_monthly` |

### Data-quality flags

| Column | Notes |
|---|---|
| `is_reapplication` | 1 = duplicate of a prior denied application (40 rows) |
| `has_timestamp_anomaly` | 1 = out-of-sequence timestamps (55 rows) |

---

## fact_condition — ~118,500 rows

Grain: **one row per condition issued.** Finer than the loan. This is the
evidence for the rework half of the argument.

| Column | Notes |
|---|---|
| `condition_id` | PK |
| `loan_id` | → `fact_loan` |
| `round_number` | Which iteration of the condition loop |
| `condition_type` | 17 types, e.g. `Income Documentation - Paystubs` |
| `condition_category` | Rolled-up type (`Income Documentation`, `Title`, …) |
| `responsible_party` | `Borrower` / `Processor` / `Third Party`. **Borrowers are the slow link** (~3.7d avg vs 1.4d for processors) |
| `issued_date`, `cleared_date`, `days_to_clear` | |
| `is_prior_to_doc` | 1 = first-round condition |

---

## fact_daily_queue — ~84,900 rows

Grain: **branch × stage × day.** Queue depth — the leading indicator that
leadership missed. Rows with zero occupancy are omitted.

| Column | Notes |
|---|---|
| `snapshot_date`, `branch_id` | |
| `stage` | `Document Collection` / `Processing` / `Appraisal` / `Underwriting` / `Condition Clearing` / `Clear to Close` |
| `loans_in_stage` | Count in that stage on that day |

> Appraisal averages ~27 loans in stage vs. underwriting's ~8 — the backlog is
> visibly sitting in appraisal, independent of any cycle-time calculation.

---

## fact_msr_monthly — ~174,900 rows

Grain: **one row per retained loan per month**, from funding to payoff or the
reporting cutoff.

### Balances & prepayment

| Column | Notes |
|---|---|
| `as_of_month`, `seasoning_months` | |
| `upb_beginning`, `upb_ending` | Unpaid principal balance |
| `scheduled_principal` | Amortization |
| `unscheduled_principal` | Prepayment / payoff |
| `note_rate`, `market_rate_30yr`, `short_rate` | |
| `rate_incentive` | `note_rate − market_rate`. **The single biggest prepay driver** |
| `annualized_cpr` | Conditional prepayment rate from the S-curve |
| `smm` | Single monthly mortality |

### Credit

| Column | Notes |
|---|---|
| `delinquency_status` | `Current` / `30 DPD` / `60 DPD` / `90+ DPD` / `Foreclosure` / `REO` / `Liquidated` |
| `months_delinquent` | Consecutive months not current |
| `escrow_advance_balance` | P&I/T&I advanced on nonperforming loans |

### Economics

| Column | Notes |
|---|---|
| `servicing_cost_annual` | Per loan. 1× current → 13× REO |
| `float_income` | Earned on escrow/payment balances. Scales with `short_rate` |
| `ancillary_income` | |
| `msr_multiple` | MSR value as a multiple of UPB (points) |
| `msr_value_bom`, `msr_value_eom` | `UPB × multiple / 100` |

### Roll-forward decomposition

These explain *why* MSR value changed, which is how a servicer reports it to a
board. They sum to the period change.

| Column | Notes |
|---|---|
| `rf_runoff` | Value lost to scheduled amortization |
| `rf_prepay` | Value lost to prepayment |
| `rf_credit_and_rate` | Multiple change from DQ migration and rate moves |
| `rf_residual` | Interaction terms |
| `is_new_addition` | 1 = loan funded this month |
| `paid_off_this_month`, `liquidated_this_month` | Exit flags |

---

## fact_msr_rate_shock — ~86,200 rows

Grain: **loan × shock bucket**, at the final reporting month. Precomputed so
the Tableau parameter responds instantly.

| Column | Notes |
|---|---|
| `rate_shock_bp` | −200 to +200 in 50bp steps (9 buckets) |
| `shocked_market_rate` | |
| `scenario_cpr`, `scenario_multiple`, `scenario_msr_value` | Re-valued under the shock |

---

## Dimensions

### dim_branch (20) / dim_branch_staffing (480)

`branch_id`, `branch_name`, `state`, `region`, `branch_size`, `opened_date`.
Staffing is monthly headcount by role — hiring lags the surge by ~3 months,
which is *why* capacity trails demand.

### dim_employee (409)

`employee_id`, `role`, `branch_id`, `region`, `experience_tier`
(Senior/Mid/Junior), `tenure_years`, `hire_date`, `capacity_index`.

> Junior underwriters issue more conditions — an input to the rework story.

### dim_geography (37)

`geo_id`, `state`, `county`, `msa`, `rural_flag`, `appraiser_panel_depth`,
`market_tier`, `panel_capacity_index`.

> `appraiser_panel_depth` ranges 3 (rural Wyoming) to 46 (Dallas). Thin panels
> are the mechanism behind the geographic finding.

### dim_vendor (4)

`vendor_id`, `vendor_name`, `vendor_type`, `coverage_states`, `sla_days`,
`is_national`. AMC-03 (Frontier) covers the rural mountain states with the
worst SLA.

### dim_rates (157)

Weekly. `week_start`, `market_rate_30yr`, `market_rate_15yr`, `short_rate`,
`rate_4wk_change`. **Drives the entire simulation.**

### dim_date (1,096)

Calendar through 2026-12-31. `date_key`, `year`, `quarter`, `month_num`,
`month_name`, `month_start`, `week_start`, `day_of_week`, `is_weekend`,
`is_holiday`, `is_business_day`, `is_month_end`, `days_to_month_end`,
`is_month_end_rush`, `fiscal_period`.

---

## Analytical Views (SQLite)

| View | Purpose |
|---|---|
| `vw_stage_cycle_time` | Stage durations by period, tier, state, channel — powers the waterfall |
| `vw_appraisal_delay_by_county` | County-level delay + excess over baseline — powers the map |
| `vw_funnel_by_month` | Applications → funded/fallout with dollar volume |
| `vw_condition_rework` | Conditions by type, party, period |
| `vw_msr_portfolio_monthly` | Portfolio UPB, MSR value, roll-forward components |
| `vw_msr_vintage_performance` | CPR and DQ by vintage × seasoning — vintage curves |
| `vw_post_closing_aging` | Trailing docs and suspense by branch/channel |
