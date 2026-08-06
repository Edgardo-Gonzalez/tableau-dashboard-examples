# Gonzalez Capital Mortgage — Mortgage Operations & Servicing Analytics

Synthetic dataset for a Tableau portfolio dashboard. Twelve tables covering
loan origination, post-closing operations, and MSR/servicing valuation for a
fictional mid-size mortgage lender across 24 months.

**All data is fabricated.** No real borrower, employee, or institutional data
is used. Everything is produced by a seeded simulation in `generator/`.

The completed workbook is available at **`tableau/GCP_Sample_Dash.twb`**.

---

## The Story

Gonzalez Capital Mortgage: ~$4B annual origination, 12 states, 3 regions, three
channels (Retail, Wholesale, Correspondent).

**The setup.** Rates drop ~110bps in Q3 2024. Application volume nearly triples
at peak. Time-to-close blows out from 36 to 51 days. Fallout doubles — a
borrower quoted 30 days who is still waiting on day 50 goes shopping.

**The assumption.** Leadership believes underwriting is the bottleneck. It is
the visible queue and the historical constraint. The proposed fix is to hire
underwriters: expensive, 90-day ramp, hard to reverse when volume normalizes.

**What the data actually shows:**

| Stage | Pre-Shock | Post-Shock | Change |
|---|---|---|---|
| Underwriting **queue wait** | 1.2d | 1.4d | **+17%** |
| Underwriting **touch time** | 4.9d | 5.6d | **+15%** |
| **Appraisal wait** | 9.0d | 20.0d | **+123%** |
| **Condition clearing** | 8.0d | 10.6d | **+33%** |
| Condition rounds per loan | 1.90 | 2.42 | **+27%** |
| Total cycle time | 36.4d | 51.0d | **+40%** |

Underwriting absorbed the surge. The damage is in two places nobody was
watching:

1. **Appraisal wait** — an *external vendor* dependency. Hiring underwriters
   does nothing to it.
2. **Condition clearing** — a *rework loop*, not a queue. Rounds rose 27%, and
   each round costs ~4 days of borrower response time. This is a quality
   problem wearing a capacity problem's clothing.

**The geographic finding.** Appraisal delay is not uniform:

- Metro markets: 8.7d → 15.1d
- Rural markets: 10.2d → **40.1d**

Rural markets are **22% of volume but 43% of total appraisal delay days**.
Thin appraiser panels get deprioritized by the AMC during surge. That converts
a vague "fix appraisal" into a targeted action in four specific states.

**The cost.** $1.1B in lost loan volume post-shock, at an 18.8% fallout rate
(up from 9.1%).

**The recommendation.** Expand the appraisal panel in four states; front-load
document collection at application to attack condition rework at the source;
do **not** mass-hire underwriters.

---

## Quick Start

```bash
# Regenerate everything (~17s, fully deterministic)
python generator/build.py

# Verify the data still tells the story (41 assertions)
python validate.py
```

Connect Tableau to either:
- **`gonzalez_capital_mortgage.db`** (SQLite) — recommended; includes 7 pre-built analytical views
- **`data/*.csv`** — 12 files, if you prefer a file-based connection

---

## Tables

| Table | Rows | Grain |
|---|---:|---|
| `fact_loan` | 22,037 | One per loan application |
| `fact_condition` | ~118,500 | One per underwriting condition issued |
| `fact_daily_queue` | ~84,900 | Branch × stage × day snapshot |
| `fact_msr_monthly` | ~174,900 | Retained loan × month |
| `fact_msr_rate_shock` | ~86,200 | Loan × rate-shock scenario (9 buckets) |
| `dim_branch` | 20 | Branch → region → state |
| `dim_branch_staffing` | 480 | Branch × month headcount |
| `dim_employee` | 409 | LO / processor / underwriter / closer |
| `dim_geography` | 37 | State → county → MSA, panel depth |
| `dim_vendor` | 4 | AMC coverage and SLA |
| `dim_rates` | 157 | Weekly 30-yr, 15-yr, short rate |
| `dim_date` | 1,096 | Calendar with holiday/business-day flags |

See [DATA_DICTIONARY.md](DATA_DICTIONARY.md) for every column.

### Analytical views (SQLite only)

`vw_stage_cycle_time`, `vw_appraisal_delay_by_county`, `vw_funnel_by_month`,
`vw_condition_rework`, `vw_msr_portfolio_monthly`, `vw_msr_vintage_performance`,
`vw_post_closing_aging`

---

## How the Simulation Works

The point of the design is that **nothing is drawn independently**. The causal
chain runs:

```
rates → application volume → queue depth → utilization → cycle time
      → fallout probability → funded population → servicing portfolio → MSR value
```

**Congestion, not sampling.** Cycle time is *computed* from queue depth against
capacity using a curve that stays flat below ~80% utilization then bends
sharply upward. A 2× volume increase produces roughly 3× wait time.

**Why the bottleneck moves on its own.** Underwriting and processing capacity
are partially elastic (overtime, contractors, lagged hiring). Appraisal
capacity is external and fixed. Nobody hard-coded "appraisal becomes the
constraint" — it falls out of that asymmetry.

**Backlog persistence.** Congestion is driven by an exponentially-weighted
accumulation of arrivals, not a rolling average. That is why a two-month
application spike becomes a two-quarter operational problem, which is what
actually happens in a rate rally.

**MSR valuation — four forces:**

1. **Rate incentive → prepayment.** S-curve with burnout: flat when out of the
   money, steep through the middle, saturating at the ceiling. Borrowers who
   repeatedly fail to act on an incentive become less rate-sensitive.
2. **Delinquency → value.** Roll-rate transition matrix. Nonperforming loans
   cost 3–13× more to service and carry advance obligations.
3. **Servicing cost.** Drifts with inflation, spikes with portfolio-wide DQ.
4. **Short rate → float income.** Earned on escrow balances — which is why
   rising rates help MSR *twice*: slower prepay AND more float.

Validated behavior at the final reporting month:

| Rate shock | MSR value | Avg CPR | Change |
|---|---|---|---|
| −200bp | $100M | 37% | −45% |
| −100bp | $133M | 24% | −27% |
| 0bp | $181M | 11% | — |
| +100bp | $189M | 6% | +4% |

That asymmetry — large downside, muted upside — is genuine MSR behavior and
worth being able to explain.

---

## Data Quality Artifacts

The data contains **deliberate, documented** real-world messiness. Every
anomaly has a plausible operational cause. See
[DATA_QUALITY.md](DATA_QUALITY.md) for the full list and how to handle each.

Summary: non-random missingness concentrated in Wholesale (5.7% vs 1.1%
Retail), a DTI units bug at one branch, 40 duplicate reapplications, 55
out-of-sequence timestamps producing negative durations, case/whitespace
variants, and a three-week AMC outage.

**Distinct from the above:** loan *characteristics* are deliberately clean and
rule-consistent. Every loan satisfies real product eligibility — conforming and
FHA/USDA limits, product- and occupancy-specific LTV caps, minimum FICO by
product, government loans on primary residences only, and government pricing
below conventional. Messiness lives in data *capture*, not in loans that could
not exist. See the eligibility table in
[DATA_DICTIONARY.md](DATA_DICTIONARY.md).

---

## Repository Layout

```
generator/
  config.py       simulation constants (all tunable)
  dimensions.py   rate curve + dimension tables
  origination.py  volume, congestion engine, stage timing, fallout
  operations.py   conditions, queue snapshots, post-closing
  msr.py          prepay S-curve, credit migration, valuation, rate shocks
  messiness.py    deliberate data-quality artifacts
  build.py        orchestrator — writes CSVs and SQLite
data/             12 CSVs
gonzalez_capital_mortgage.db  SQLite with indexes and analytical views
validate.py       29 assertions tying the data back to the story
docs/superpowers/specs/   design document
```
