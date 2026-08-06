"""Supporting fact tables: conditions, daily queue snapshots, post-closing.

`fact_condition` is what turns "cycle time got worse" into "rework got worse" --
it is the evidence for the second half of the argument.
"""

import numpy as np
import pandas as pd

import config as C

# Condition types, weighted. Income/asset conditions dominate and are the ones
# that get worse when document intake is rushed -- which is the actionable
# finding: front-load document collection at application.
CONDITION_TYPES = [
    ("Income Documentation - Paystubs", 0.132, "Borrower"),
    ("Income Documentation - Tax Returns", 0.118, "Borrower"),
    ("Asset Verification - Bank Statements", 0.126, "Borrower"),
    ("Asset Verification - Large Deposit LOE", 0.071, "Borrower"),
    ("Employment Verification - VOE", 0.084, "Processor"),
    ("Credit - Letter of Explanation", 0.079, "Borrower"),
    ("Appraisal - Repair Required", 0.048, "Third Party"),
    ("Appraisal - Value Reconsideration", 0.031, "Third Party"),
    ("Title - Lien Release Required", 0.043, "Third Party"),
    ("Title - Vesting Correction", 0.028, "Third Party"),
    ("Insurance - HOI Binder", 0.056, "Borrower"),
    ("Insurance - Flood Certificate", 0.024, "Processor"),
    ("Compliance - TRID Redisclosure", 0.037, "Processor"),
    ("Compliance - Signature Missing", 0.033, "Borrower"),
    ("Gift Funds - Donor Letter", 0.029, "Borrower"),
    ("HOA - Certification", 0.022, "Third Party"),
    ("Self-Employment - P&L Statement", 0.039, "Borrower"),
]

# Response time by responsible party. Borrowers are the slow link -- which is
# why more condition rounds translate directly into more calendar days.
PARTY_RESPONSE_DAYS = {
    "Borrower": 3.6,
    "Processor": 1.2,
    "Third Party": 4.4,
}


def build_fact_condition(loans: pd.DataFrame) -> pd.DataFrame:
    """One row per condition issued. Grain is finer than the loan, so this
    supports 'conditions per loan' and 'days per condition type' analysis."""
    r = C.rng(20)

    # Only loans that reached conditional approval have conditions.
    elig = loans[loans.conditional_approval_date.notna()].copy()
    elig = elig[elig.condition_rounds > 0]

    types = [t[0] for t in CONDITION_TYPES]
    weights = np.array([t[1] for t in CONDITION_TYPES])
    weights = weights / weights.sum()
    party_map = {t[0]: t[2] for t in CONDITION_TYPES}

    # Conditions per round: 1-4, more when the file is complex.
    rows = []
    cond_seq = 0

    loan_ids = elig.loan_id.values
    approval = elig.conditional_approval_date.values
    cleared = elig.final_conditions_cleared.values
    rounds_arr = elig.condition_rounds.values.astype(int)
    selfemp = (elig.employment_type == "Self-Employed").values
    wholesale = (elig.channel == "Wholesale").values
    total_cond_days = elig._condition_days.values

    for i in range(len(elig)):
        n_rounds = rounds_arr[i]
        if n_rounds <= 0:
            continue
        start = pd.Timestamp(approval[i])
        span = total_cond_days[i] if not np.isnan(total_cond_days[i]) else n_rounds * 4.0
        per_round = span / max(n_rounds, 1)

        for rd in range(1, n_rounds + 1):
            n_conditions = 1 + r.poisson(1.15 + 0.5 * selfemp[i] + 0.3 * wholesale[i])
            n_conditions = int(np.clip(n_conditions, 1, 6))
            round_start = start + pd.Timedelta(days=float(per_round * (rd - 1)))

            chosen = r.choice(len(types), size=n_conditions, replace=False, p=weights)
            for ci in chosen:
                cond_seq += 1
                ctype = types[ci]
                party = party_map[ctype]
                base_resp = PARTY_RESPONSE_DAYS[party]
                resp = base_resp * r.lognormal(0, 0.52)
                issued = round_start + pd.Timedelta(days=float(r.uniform(0, 0.4)))
                cleared_dt = issued + pd.Timedelta(days=float(resp))
                # Cap at the loan's actual clearing date where known.
                if not pd.isna(cleared[i]):
                    cap = pd.Timestamp(cleared[i])
                    if cleared_dt > cap:
                        cleared_dt = cap

                rows.append(
                    {
                        "condition_id": f"CD-{cond_seq:07d}",
                        "loan_id": loan_ids[i],
                        "round_number": rd,
                        "condition_type": ctype,
                        "condition_category": ctype.split(" - ")[0],
                        "responsible_party": party,
                        "issued_date": issued,
                        "cleared_date": cleared_dt,
                        "days_to_clear": round(
                            (cleared_dt - issued).total_seconds() / 86400.0, 2
                        ),
                        "is_prior_to_doc": int(rd == 1),
                    }
                )

    return pd.DataFrame(rows)


def build_fact_daily_queue(loans: pd.DataFrame) -> pd.DataFrame:
    """Daily count of loans sitting in each stage, by branch.

    This is what lets the dashboard show queue depth building ahead of cycle
    time deteriorating -- the leading indicator that leadership missed.
    """
    days = pd.date_range(C.START_DATE, C.END_DATE, freq="D")

    # Stage occupancy windows: (stage label, entry column, exit column)
    stages = [
        ("Document Collection", "application_date", "docs_received"),
        ("Processing", "processing_start", "processing_end"),
        ("Appraisal", "appraisal_ordered", "appraisal_received"),
        ("Underwriting", "underwriting_start", "underwriting_end"),
        ("Condition Clearing", "conditional_approval_date", "final_conditions_cleared"),
        ("Clear to Close", "ctc_date", "funded_date"),
    ]

    frames = []
    for stage_name, entry_col, exit_col in stages:
        sub = loans[[entry_col, exit_col, "branch_id", "region"]].dropna(subset=[entry_col])
        if sub.empty:
            continue
        entry = sub[entry_col].dt.normalize()
        # Loans with no exit are still in the stage at the reporting cutoff.
        exit_ = sub[exit_col].fillna(pd.Timestamp(C.END_DATE)).dt.normalize()

        # Build per-branch daily counts by accumulating +1 at entry, -1 at exit.
        tmp = pd.DataFrame(
            {"branch_id": sub.branch_id.values, "entry": entry.values, "exit": exit_.values}
        )
        for branch, g in tmp.groupby("branch_id"):
            inc = g.groupby("entry").size().reindex(days, fill_value=0)
            dec = g.groupby("exit").size().reindex(days, fill_value=0)
            occupancy = (inc - dec).cumsum().clip(lower=0)
            frames.append(
                pd.DataFrame(
                    {
                        "snapshot_date": days,
                        "branch_id": branch,
                        "stage": stage_name,
                        "loans_in_stage": occupancy.values.astype(int),
                    }
                )
            )

    q = pd.concat(frames, ignore_index=True)
    # Trim the long tail of all-zero rows to keep the file manageable while
    # preserving every day a branch actually had work in a stage.
    q = q[q.loans_in_stage > 0].reset_index(drop=True)
    return q


def build_post_closing(loans: pd.DataFrame) -> pd.DataFrame:
    """Post-closing fields for funded loans: funding type, title, trailing docs,
    investor delivery, suspense.

    Powers dashboard tab 2. Deliberately independent of the origination
    bottleneck story so the two narratives do not contaminate each other.
    """
    r = C.rng(21)
    df = loans.copy()
    funded = df.status == "Funded"
    n = len(df)

    # --- wet vs dry funding: a state-law characteristic ---
    # Dry funding states require documents reviewed before disbursement.
    dry_states = {"NM", "WY", "MT"}
    is_dry = df.property_state.isin(dry_states) | (r.random(n) < 0.08)
    df["funding_type"] = np.where(funded, np.where(is_dry, "Dry", "Wet"), None)

    fd = df.funded_date

    # --- title policy ---
    title_lag = r.lognormal(np.log(16), 0.55, n)
    # Rural counties have slower recording offices.
    title_lag *= np.where(df.rural_flag == 1, 1.38, 1.0)
    df["title_policy_received_date"] = fd + pd.to_timedelta(np.round(title_lag, 0), unit="D")

    # --- recording ---
    rec_lag = np.clip(r.lognormal(np.log(6), 0.5, n), 1, 60)
    rec_lag *= np.where(df.rural_flag == 1, 1.45, 1.0)
    df["recording_date"] = fd + pd.to_timedelta(np.round(rec_lag, 0), unit="D")

    # --- final docs ---
    final_lag = np.clip(r.lognormal(np.log(28), 0.62, n), 5, 220)
    final_lag *= np.where(df.channel == "Wholesale", 1.32, 1.0)
    df["final_docs_received_date"] = fd + pd.to_timedelta(np.round(final_lag, 0), unit="D")

    # --- investor delivery & purchase advice ---
    deliv_lag = np.clip(r.lognormal(np.log(11), 0.48, n), 2, 90)
    df["investor_delivery_date"] = fd + pd.to_timedelta(np.round(deliv_lag, 0), unit="D")
    pa_lag = np.clip(r.lognormal(np.log(8), 0.55, n), 1, 75)
    df["purchase_advice_date"] = df.investor_delivery_date + pd.to_timedelta(
        np.round(pa_lag, 0), unit="D"
    )

    # --- suspense: investor rejects the file pending a fix ---
    p_susp = (
        0.052
        + 0.030 * (df.channel == "Wholesale")
        + 0.018 * (df.rural_flag == 1)
        + 0.022 * (df.loan_type.isin(["FHA", "USDA"]))
    )
    suspense = (r.random(n) < p_susp) & funded
    df["suspense_flag"] = np.where(funded, suspense.astype(int), None)
    df["suspense_reason"] = np.where(suspense, r.choice(C.SUSPENSE_REASONS, n), None)
    days_susp = np.where(suspense, np.round(np.clip(r.lognormal(np.log(9), 0.7, n), 1, 90)), 0)
    df["days_in_suspense"] = np.where(funded, days_susp, None)

    # --- trailing docs status, as of the reporting cutoff ---
    cutoff = pd.Timestamp(C.END_DATE)
    title_in = df.title_policy_received_date <= cutoff
    final_in = df.final_docs_received_date <= cutoff

    status = np.where(
        ~funded,
        None,
        np.where(
            suspense & (r.random(n) < 0.55),
            "Exception",
            np.where(
                ~title_in,
                "Pending Title Policy",
                np.where(
                    ~final_in,
                    "Pending Final Docs",
                    np.where(
                        (df.ltv > 80) & (df.loan_type == "Conventional") & (r.random(n) < 0.06),
                        "Pending MI Certificate",
                        "Complete",
                    ),
                ),
            ),
        ),
    )
    df["trailing_docs_status"] = status

    # Aging on open items, measured from funding to the reporting cutoff.
    open_item = funded & (df.trailing_docs_status != "Complete")
    age = (cutoff - fd).dt.days
    df["trailing_docs_age_days"] = np.where(open_item, age, None)

    # --- first payment ---
    fp = fd + pd.offsets.MonthBegin(2)
    df["first_payment_date"] = np.where(funded, fp, pd.NaT)

    # --- escrow ---
    df["escrow_flag"] = np.where(
        funded,
        np.where((df.ltv > 80) | (r.random(n) < 0.62), 1, 0),
        None,
    )

    # --- servicing retention decision ---
    retained = funded & (r.random(n) < C.SERVICING_RETAINED_SHARE)
    df["servicing_status"] = np.where(
        funded, np.where(retained, "Retained", r.choice(["Released", "Sold"], n, p=[0.55, 0.45])), None
    )

    # Null out post-closing fields for non-funded loans.
    postclose_cols = [
        "title_policy_received_date", "recording_date", "final_docs_received_date",
        "investor_delivery_date", "purchase_advice_date", "first_payment_date",
    ]
    for c in postclose_cols:
        df.loc[~funded, c] = pd.NaT

    return df
