# Meridian Home Lending — Tableau Portfolio Dashboard

**Date:** 2026-08-06
**Status:** Approved — data generation phase

## Purpose

A portfolio artifact demonstrating Tableau proficiency on synthetic mortgage-lending
data. Optimized for a hiring-manager audience: immediate visual impact on the primary
tab, with analytical depth available on demand for technical follow-up.

All data is fabricated. No real borrower, employee, or company data is involved.

## Scenario

Meridian Home Lending is a fictional mid-size lender: ~$4B annual origination,
12 states, 3 regions, three channels (Retail, Wholesale, Correspondent).

**The setup.** Rates drop ~110bps in Q3 of Year 1. Application volume nearly doubles
over eight weeks. Time-to-close blows out from 32 to 51 days. Fallout climbs, because
a borrower quoted 30 days who is still waiting on day 50 goes shopping.

**The assumption.** Leadership believes underwriting is the bottleneck — it is the
visible queue and the historical constraint. The proposed fix is hiring underwriters:
expensive, 90-day ramp, hard to reverse when volume normalizes.

**The reveal.** Stage decomposition shows underwriting absorbed the surge reasonably
(median touch time 4.2 → 6.1 days). The damage sits in two unwatched places:

1. **Appraisal wait** — external vendor dependency, 8 → 19 days. Hiring underwriters
   does nothing to this.
2. **Condition clearing** — a rework loop, not a queue. Condition rounds rose 1.8 →
   3.1, each costing ~4 days of borrower response time.

**The geographic finding.** Appraisal delay is not uniform. Metro-dense states
(TX, AZ, CO, NC) went 8 → 13 days. Rural-heavy markets (MT, WY, NM, rural KS/NE)
went 9 → 31 days — thin appraiser panels, deprioritized by the AMC during surge.
~22% of volume sits in those markets but contributes ~48% of appraisal delay days.
This converts "fix appraisal" into a targeted, cheap action.

**The action.** Expand appraisal panel in four specific states; attack condition
rework by front-loading document collection at application; do *not* mass-hire
underwriters. Quantified with fallout dollars at stake.

## Data Model

Star schema. One primary fact table, three supporting fact tables, six dimensions.

### fact_loan (~22,000 rows, one per application, 24 months)

- **Identity/routing:** loan_id, application_date, channel, branch_id,
  loan_officer_id, underwriter_id, processor_id, amc_vendor_id
- **Characteristics:** loan_purpose, loan_type, occupancy, property_type,
  loan_amount, appraised_value, ltv, cltv, dti, fico, note_rate, lock_date,
  lock_expiration, lock_extensions, employment_type
- **Stage timestamps** (entry + exit per stage, enabling wait-vs-touch separation):
  app_submitted, docs_received, processing_start/end, appraisal_ordered,
  appraisal_received, underwriting_start/end, conditional_approval_date,
  condition_rounds, final_conditions_cleared, ctc_date, closing_scheduled,
  funded_date, status, fallout_reason
- **Post-closing:** funding_type (Wet/Dry), title_policy_received_date,
  final_docs_received_date, trailing_docs_status, recording_date,
  investor_delivery_date, purchase_advice_date, suspense_flag, suspense_reason,
  days_in_suspense, first_payment_date
- **Servicing linkage:** servicing_status (Retained/Released/Sold), escrow_flag

### fact_msr_monthly (~130–150k rows)

Grain: one row per funded-and-retained loan per month, funding through payoff.

loan_id, as_of_month, upb_beginning/ending, scheduled_principal,
unscheduled_principal, note_rate, market_rate_30yr, rate_incentive, smm,
annualized_cpr, delinquency_status, months_delinquent, servicing_cost_annual,
escrow_advance_balance, float_income, ancillary_income, msr_multiple,
msr_value_bom/eom, plus roll-forward decomposition columns.

### fact_condition

One row per condition issued: loan_id, condition_type, issued_date, cleared_date,
responsible_party, round_number. Powers the rework analysis.

### fact_daily_queue

Daily snapshot of loans in each stage per branch. Provides queue depth for the
congestion model and lets the dashboard show queue buildup directly.

### Dimensions

- **dim_branch** — branch → region → state, monthly staffing headcount
- **dim_geography** — state, county, MSA, rural_flag, appraiser_panel_depth
- **dim_employee** — role, hire date, experience tier, capacity
- **dim_vendor** — AMC name, coverage states, SLA days
- **dim_date** — calendar, holiday flags, business-day flags, month-end markers
- **dim_rates** — weekly average 30-yr rate; drives the entire simulation

## Simulation Design

Python (pandas/numpy), seeded for reproducibility.

**Core principle: nothing is drawn independently.** Causal chain:
rates → application volume → queue depth → cycle time (congestion curve) →
fallout probability → funded population → servicing portfolio → MSR value.

### Congestion model

Cycle time is computed from queue depth against capacity, not sampled from a
distribution. The curve stays flat below ~80% utilization then bends sharply
upward. A 2× volume increase yields ~3× wait time. The bottleneck *moves* because
appraisal capacity is externally fixed while underwriting capacity gets partially
backfilled via overtime and contractors.

### MSR valuation — four forces

1. **Rate incentive → prepay.** S-curve with burnout. Near-zero incentive: 6–8%
   CPR. Past ~75bps in the money, accelerates toward 40–50% CPR, then flattens as
   remaining borrowers prove rate-insensitive. Faster prepay shortens expected life
   and compresses the multiple.
2. **Delinquency → value.** DQ loans cost 3–4× (30–60 DPD) to 8–10× (90+/FC) to
   service, and carry P&I/T&I advance obligations. Serious DQ on government loans
   risks losing the servicing outright.
3. **Servicing cost per loan.** Drifts with inflation, spikes with portfolio DQ,
   shows scale effects as the portfolio grows.
4. **Rate level → float income.** Higher rates increase float on escrow and payment
   balances — so rising rates help twice (slower prepay + more float). Two-sided
   sensitivity.

### Credit model

Roll-rate transition matrix. Monthly migration with realistic probabilities
(Current→30 ≈ 1.2%/mo baseline, 30→60 ≈ 35%, 60→90 ≈ 55%, plus cure paths).
Rates vary by FICO band, LTV, loan type, occupancy, and seasoning; defaults peak
around months 18–30. Produces authentic vintage curves.

### Rate-shock scenarios

Precomputed MSR values at nine shock buckets (−200 to +200bps in 50bps steps) so
the Tableau parameter control responds instantly.

### Deliberate messiness — all explainable

- 3-week AMC vendor outage in month 14
- ~2% missing values, non-randomly distributed (Wholesale worse than Retail)
- ~40 duplicate applications (reapply after denial), near-identical records
- One branch entering dti as decimal (0.42) rather than percentage (42) for a stretch
- Weekend/holiday timestamp gaps, month-end funding rush, December slump
- A small set of loans with out-of-sequence timestamps

## Dashboards

**Tab 1 — "Where Did The Time Go?"** (the screenshot piece)
KPI strip → stage-decomposition waterfall (before/after shock) → wait-vs-touch split
by stage → filled map of appraisal delay by county → condition-rework trend →
fallout-dollars callout. Annotated with the argument, ending in three recommendations.

**Tab 2 — "Post-Closing Operations"**
Trailing-doc aging buckets, exception rate by branch, wet vs. dry funding mix, title
turnaround distribution, suspense reasons ranked, investor delivery timeliness.

**Tab 3 — "Servicing & MSR"**
Portfolio UPB and MSR value over time, roll-forward decomposition, prepay S-curve
(actual CPR vs. rate incentive), DQ roll-rate matrix, vintage curves, interactive
rate-shock parameter.

### Advanced mechanics deliberately included

Nested LOD expressions for stage-level medians; table calculations for roll-forward
and cohort retention; parameter-driven what-if; dashboard actions linking tabs; set
actions for cohort comparison; custom SQL views in the SQLite connection.

## Deliverables

1. `generate_data.py` — seeded simulation
2. Nine CSVs in `/data`
3. `meridian.db` — SQLite with tables, indexes, analytical views
4. `BUILD_GUIDE.md` — sheet-by-sheet: calculated fields in full Tableau syntax,
   mark types, color specs, layout, annotation copy
5. `README.md` — scenario framing and talking points

## Build Sequencing

Tab 1 first and complete, then screenshot. It is self-contained and is the portfolio
piece. Tabs 2 and 3 are interview depth. Estimated Tableau assembly: 3–4 hours across
all three tabs.

## Out of Scope

- Automated `.twb` generation (Tableau assembly is manual)
- Any real or real-derived data
