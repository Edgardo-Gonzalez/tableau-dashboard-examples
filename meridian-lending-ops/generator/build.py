"""Entry point: runs the full simulation and writes CSVs + SQLite.

    python generator/build.py

Everything is seeded, so repeated runs produce byte-identical output.
"""

import os
import sqlite3
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config as C
import dimensions as D
import origination as O
import operations as OPS
import msr as M
import messiness as MESS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
DB_PATH = os.path.join(ROOT, "meridian.db")


# Internal working columns (leading underscore) are dropped before export --
# they are model intermediates, not things a BI user should see. The derived
# stage-duration columns below replace them with clean, documented equivalents.
def finalize_loan_table(loans: pd.DataFrame) -> pd.DataFrame:
    df = loans.copy()

    # Published stage durations, computed from timestamps so they stay
    # consistent with whatever a Tableau user computes themselves.
    def days_between(a, b):
        return ((df[b] - df[a]).dt.total_seconds() / 86400.0).round(2)

    df["days_app_to_docs"] = days_between("application_date", "docs_received")
    df["days_processing"] = days_between("processing_start", "processing_end")
    df["days_appraisal_wait"] = days_between("appraisal_ordered", "appraisal_received")

    # Underwriting cannot start until BOTH processing is done and the appraisal
    # is back. Measuring the UW queue from processing_end alone would silently
    # attribute appraisal delay to underwriting -- the exact misdiagnosis this
    # dashboard exists to correct. Measure from the true gate instead.
    uw_ready = df[["processing_end", "appraisal_received"]].max(axis=1)
    df["uw_ready_date"] = uw_ready
    df["days_uw_queue_wait"] = (
        (df.underwriting_start - uw_ready).dt.total_seconds() / 86400.0
    ).round(2)
    df["days_uw_touch"] = days_between("underwriting_start", "underwriting_end")
    df["days_condition_clearing"] = days_between(
        "conditional_approval_date", "final_conditions_cleared"
    )
    df["days_ctc_to_funding"] = days_between("ctc_date", "funded_date")
    df["days_total_cycle"] = days_between("application_date", "funded_date")

    # Flag the pre/post windows the narrative uses, so the dashboard does not
    # have to hardcode dates in a dozen calculated fields.
    df["shock_period"] = np.select(
        [
            df.application_date <= pd.Timestamp(C.PRE_SHOCK_END),
            df.application_date < pd.Timestamp(C.POST_SHOCK_START),
        ],
        ["Pre-Shock", "During Shock"],
        default="Post-Shock",
    )

    df["market_tier"] = np.where(df.rural_flag == 1, "Rural", "Metro")

    ordered = [
        # identity
        "loan_id", "application_date", "status", "shock_period",
        # routing
        "channel", "branch_id", "branch_name", "region", "branch_size",
        "loan_officer_id", "processor_id", "underwriter_id", "closer_id",
        "amc_vendor_id",
        # property / geography
        "geo_id", "property_state", "property_county", "property_msa",
        "market_tier", "rural_flag", "appraiser_panel_depth",
        "property_type", "occupancy",
        # loan characteristics
        "loan_purpose", "loan_type", "loan_amount", "appraised_value",
        "ltv", "cltv", "dti", "fico", "employment_type", "note_rate",
        "lock_date", "lock_term_days", "lock_expiration", "lock_extensions",
        # stage timestamps
        "docs_received", "processing_start", "processing_end",
        "appraisal_ordered", "appraisal_received",
        "uw_ready_date", "underwriting_start", "underwriting_end",
        "conditional_approval_date",
        "condition_rounds", "final_conditions_cleared", "ctc_date",
        "closing_scheduled", "funded_date",
        # derived durations
        "days_app_to_docs", "days_processing", "days_appraisal_wait",
        "days_uw_queue_wait", "days_uw_touch", "days_condition_clearing",
        "days_ctc_to_funding", "days_total_cycle",
        # fallout
        "fallout_reason", "fallout_stage",
        # post-closing
        "funding_type", "title_policy_received_date", "recording_date",
        "final_docs_received_date", "investor_delivery_date",
        "purchase_advice_date", "trailing_docs_status", "trailing_docs_age_days",
        "suspense_flag", "suspense_reason", "days_in_suspense",
        "first_payment_date", "escrow_flag", "servicing_status",
        # data-quality markers
        "is_reapplication", "has_timestamp_anomaly",
    ]
    cols = [c for c in ordered if c in df.columns]
    return df[cols]


def add_lock_extensions(loans: pd.DataFrame) -> pd.DataFrame:
    """Lock extensions are a direct consequence of the cycle-time blowout --
    a loan that outruns its lock has to pay to extend it. This connects the
    operational problem to a real dollar cost."""
    r = C.rng(50)
    df = loans.copy()
    ref = df.funded_date.fillna(df.ctc_date).fillna(df.underwriting_end)
    overrun = (ref - df.lock_expiration).dt.total_seconds() / 86400.0
    # Each extension buys ~15 days.
    ext = np.ceil(np.clip(overrun, 0, None) / 15.0)
    # A little noise: some are renegotiated, some relocked.
    ext = np.where(r.random(len(df)) < 0.12, np.clip(ext - 1, 0, None), ext)
    df["lock_extensions"] = np.nan_to_num(ext, nan=0).astype(int)
    return df


def write_csvs(tables: dict):
    os.makedirs(DATA_DIR, exist_ok=True)
    for name, df in tables.items():
        path = os.path.join(DATA_DIR, f"{name}.csv")
        df.to_csv(path, index=False, date_format="%Y-%m-%d")
        size_mb = os.path.getsize(path) / 1e6
        print(f"  {name+'.csv':32s} {len(df):>9,} rows   {size_mb:6.1f} MB")


def write_sqlite(tables: dict):
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    con = sqlite3.connect(DB_PATH)

    for name, df in tables.items():
        out = df.copy()
        # SQLite has no date type; ISO strings sort and filter correctly and
        # Tableau parses them natively.
        for c in out.columns:
            if out[c].dtype.kind == "M":
                out[c] = out[c].dt.strftime("%Y-%m-%d")
        out.to_sql(name, con, index=False, if_exists="replace")

    cur = con.cursor()
    indexes = [
        ("idx_loan_id", "fact_loan", "loan_id"),
        ("idx_loan_appdate", "fact_loan", "application_date"),
        ("idx_loan_branch", "fact_loan", "branch_id"),
        ("idx_loan_state", "fact_loan", "property_state"),
        ("idx_loan_status", "fact_loan", "status"),
        ("idx_cond_loan", "fact_condition", "loan_id"),
        ("idx_cond_type", "fact_condition", "condition_type"),
        ("idx_msr_loan", "fact_msr_monthly", "loan_id"),
        ("idx_msr_month", "fact_msr_monthly", "as_of_month"),
        ("idx_queue_date", "fact_daily_queue", "snapshot_date"),
        ("idx_shock_bucket", "fact_msr_rate_shock", "rate_shock_bp"),
    ]
    for idx_name, table, col in indexes:
        try:
            cur.execute(f"CREATE INDEX {idx_name} ON {table}({col})")
        except sqlite3.OperationalError as e:
            print(f"  ! index {idx_name}: {e}")

    # Analytical views -- these are what makes the SQLite connection worth
    # demoing. Tableau can connect to a view exactly like a table, so the
    # dashboard can consume pre-shaped analysis instead of raw rows.
    views = {
        "vw_stage_cycle_time": """
            SELECT shock_period, market_tier, property_state, channel,
                   region, branch_name, loan_purpose,
                   COUNT(*)                        AS loans,
                   AVG(days_app_to_docs)           AS avg_doc_days,
                   AVG(days_processing)            AS avg_processing_days,
                   AVG(days_appraisal_wait)        AS avg_appraisal_wait,
                   AVG(days_uw_queue_wait)         AS avg_uw_wait,
                   AVG(days_uw_touch)              AS avg_uw_touch,
                   AVG(days_condition_clearing)    AS avg_condition_days,
                   AVG(days_ctc_to_funding)        AS avg_ctc_days,
                   AVG(days_total_cycle)           AS avg_total_cycle,
                   AVG(condition_rounds)           AS avg_condition_rounds
            FROM fact_loan
            WHERE status = 'Funded' AND days_total_cycle > 0
            GROUP BY shock_period, market_tier, property_state, channel,
                     region, branch_name, loan_purpose
        """,
        "vw_appraisal_delay_by_county": """
            SELECT property_state, property_county, market_tier,
                   appraiser_panel_depth, shock_period,
                   COUNT(*)                                  AS loans,
                   AVG(days_appraisal_wait)                  AS avg_wait,
                   SUM(days_appraisal_wait)                  AS total_wait_days,
                   AVG(days_appraisal_wait) - 8.0            AS excess_over_baseline
            FROM fact_loan
            WHERE days_appraisal_wait IS NOT NULL
            GROUP BY property_state, property_county, market_tier,
                     appraiser_panel_depth, shock_period
        """,
        "vw_funnel_by_month": """
            SELECT substr(application_date, 1, 7)                        AS app_month,
                   channel, region, market_tier,
                   COUNT(*)                                              AS applications,
                   SUM(CASE WHEN status='Funded'     THEN 1 ELSE 0 END)  AS funded,
                   SUM(CASE WHEN status='Fallout'    THEN 1 ELSE 0 END)  AS fallout,
                   SUM(CASE WHEN status='In Process' THEN 1 ELSE 0 END)  AS in_process,
                   SUM(CASE WHEN status='Funded' THEN loan_amount ELSE 0 END) AS funded_volume,
                   SUM(CASE WHEN status='Fallout' THEN loan_amount ELSE 0 END) AS lost_volume
            FROM fact_loan
            GROUP BY app_month, channel, region, market_tier
        """,
        "vw_condition_rework": """
            SELECT c.condition_category, c.condition_type, c.responsible_party,
                   l.shock_period, l.channel, l.employment_type,
                   COUNT(*)                    AS condition_count,
                   AVG(c.days_to_clear)        AS avg_days_to_clear,
                   AVG(c.round_number)         AS avg_round,
                   COUNT(DISTINCT c.loan_id)   AS loans_affected
            FROM fact_condition c
            JOIN fact_loan l ON l.loan_id = c.loan_id
            GROUP BY c.condition_category, c.condition_type, c.responsible_party,
                     l.shock_period, l.channel, l.employment_type
        """,
        "vw_msr_portfolio_monthly": """
            SELECT as_of_month,
                   COUNT(*)                              AS loan_count,
                   SUM(upb_ending)                       AS total_upb,
                   SUM(msr_value_eom)                    AS total_msr_value,
                   AVG(msr_multiple)                     AS avg_multiple,
                   AVG(annualized_cpr)                   AS avg_cpr,
                   AVG(market_rate_30yr)                 AS market_rate,
                   SUM(rf_runoff)                        AS rf_runoff,
                   SUM(rf_prepay)                        AS rf_prepay,
                   SUM(rf_credit_and_rate)               AS rf_credit_and_rate,
                   SUM(servicing_cost_annual) / 12.0     AS monthly_servicing_cost,
                   SUM(float_income)                     AS float_income,
                   SUM(CASE WHEN delinquency_status <> 'Current'
                            THEN upb_ending ELSE 0 END)  AS delinquent_upb
            FROM fact_msr_monthly
            WHERE upb_ending > 0
            GROUP BY as_of_month
        """,
        "vw_msr_vintage_performance": """
            SELECT substr(l.funded_date, 1, 7)  AS vintage_month,
                   m.seasoning_months,
                   COUNT(*)                     AS loans,
                   AVG(m.annualized_cpr)        AS avg_cpr,
                   AVG(m.msr_multiple)          AS avg_multiple,
                   SUM(CASE WHEN m.delinquency_status <> 'Current' THEN 1 ELSE 0 END) * 1.0
                       / COUNT(*)               AS dq_rate
            FROM fact_msr_monthly m
            JOIN fact_loan l ON l.loan_id = m.loan_id
            WHERE m.upb_ending > 0
            GROUP BY vintage_month, m.seasoning_months
        """,
        "vw_post_closing_aging": """
            SELECT trailing_docs_status, funding_type, channel, region,
                   branch_name, market_tier,
                   COUNT(*)                          AS loans,
                   AVG(trailing_docs_age_days)       AS avg_age_days,
                   SUM(CASE WHEN suspense_flag = 1 THEN 1 ELSE 0 END) AS suspense_count,
                   AVG(days_in_suspense)             AS avg_days_in_suspense
            FROM fact_loan
            WHERE status = 'Funded'
            GROUP BY trailing_docs_status, funding_type, channel, region,
                     branch_name, market_tier
        """,
    }
    for vname, sql in views.items():
        cur.execute(f"CREATE VIEW {vname} AS {sql}")

    con.commit()

    print(f"\n  SQLite: {os.path.basename(DB_PATH)} "
          f"({os.path.getsize(DB_PATH)/1e6:.1f} MB)")
    print(f"  {len(tables)} tables, {len(views)} analytical views, "
          f"{len(indexes)} indexes")
    con.close()


def main():
    t0 = time.time()
    print("Meridian Home Lending - synthetic data generation")
    print("=" * 62)

    print("\n[1/6] Dimensions...")
    dims = D.build_all()
    daily_rate = D.daily_rate_lookup(dims["dim_rates"])

    print("[2/6] Origination pipeline...")
    loans, volume = O.build(dims, daily_rate)

    print("[3/6] Post-closing...")
    loans = OPS.build_post_closing(loans)
    loans = add_lock_extensions(loans)

    print("[4/6] Conditions + queue snapshots...")
    conditions = OPS.build_fact_condition(loans)
    queue = OPS.build_fact_daily_queue(loans)

    print("[5/6] MSR monthly panel + rate shock scenarios...")
    msr_panel = M.build_msr_panel(loans, dims["dim_rates"])
    shock = M.build_rate_shock_scenarios(loans, msr_panel)

    print("[6/6] Injecting data-quality artifacts...")
    loans_clean = loans.copy()
    loans = MESS.apply_all(loans)
    loans = finalize_loan_table(loans)

    tables = {
        "fact_loan": loans,
        "fact_condition": conditions,
        "fact_daily_queue": queue,
        "fact_msr_monthly": msr_panel,
        "fact_msr_rate_shock": shock,
        "dim_branch": dims["dim_branch"],
        "dim_branch_staffing": dims["dim_branch_staffing"],
        "dim_employee": dims["dim_employee"],
        "dim_geography": dims["dim_geography"],
        "dim_vendor": dims["dim_vendor"],
        "dim_rates": dims["dim_rates"],
        "dim_date": dims["dim_date"],
    }

    print("\nWriting CSVs...")
    write_csvs(tables)

    print("\nWriting SQLite database...")
    write_sqlite(tables)

    print(f"\nDone in {time.time()-t0:.1f}s")
    return tables, loans_clean


if __name__ == "__main__":
    main()
