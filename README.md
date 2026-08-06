# tableau-dashboard-examples

Location for work dedicated to creating Tableau dashboards based on my
real-life work. Contains workbook files, SQL files, data generators, and Python
scripts used for generating synthetic data.

Each project includes its data generator, a validation harness, and full
documentation — so every number on every dashboard is reproducible from source.

**All data in this repository is fabricated.** No real customer, employee, or
institutional data is used anywhere.

---

## Projects

### [gonzalez-capital-mortgage/](gonzalez-capital-mortgage/) — Mortgage Operations & Servicing

A mid-size mortgage lender absorbs a rate-driven volume surge. Leadership
assumes underwriting is the bottleneck and prepares to hire underwriters. The
data shows the constraint actually moved to appraisal — an external vendor
dependency — and to condition rework, a quality problem wearing a capacity
problem's clothing.

| Stage | Pre-Shock | Post-Shock | Change |
|---|---|---|---|
| Underwriting queue wait | 1.2d | 1.4d | +17% |
| Underwriting touch time | 4.9d | 5.6d | +16% |
| **Appraisal wait** | 9.0d | 20.0d | **+122%** |
| **Condition clearing** | 8.0d | 10.6d | **+33%** |
| Total cycle time | 36.4d | 51.0d | +40% |

Rural markets are 22% of volume but 43% of appraisal delay days. Fallout
doubled to 18.8%, costing $1.1B in lost loan volume.

**Scope:** 22k loans over 24 months, 12 tables, ~370k rows. Includes a monthly
MSR valuation panel with prepayment S-curves, roll-rate credit migration, and
an interactive rate-shock scenario model.

**Demonstrates:** queueing/congestion modeling, stage decomposition (wait vs.
touch time), geographic concentration analysis, MSR mark-to-market with
roll-forward attribution, and deliberate data-quality handling.

---

## Working With These Projects

Generated data is **not committed** — it is fully reproducible from seeded
scripts, so committing it would bloat the repository for no benefit.

```bash
cd gonzalez-capital-mortgage
python generator/build.py    # regenerate all data (~19s, deterministic)
python validate.py           # 41 assertions tying data back to the story
```

Then connect Tableau to the generated `gonzalez_capital_mortgage.db` (SQLite, includes
pre-built analytical views) or to the CSVs in `data/`.

**Requirements:** Python 3.10+, pandas, numpy.
