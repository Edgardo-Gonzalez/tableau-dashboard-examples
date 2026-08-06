"""Deliberate real-world data-quality artifacts.

Every anomaly here is defensible: it has a plausible operational cause, and it
is documented in DATA_QUALITY.md so it can be discussed rather than explained
away. This is applied LAST, after all analytics are computed, so the underlying
model stays clean while the delivered CSVs look like real extracts.

Applied to the loan table only. The MSR panel is left clean, on the reasoning
that servicing data comes from a different system of record with tighter
controls -- which is itself a realistic detail.
"""

import numpy as np
import pandas as pd

import config as C


# Fields eligible to go missing, with per-field relative likelihood. Keys that
# matter for joins or the core narrative are excluded -- a portfolio dashboard
# should degrade gracefully, not break.
NULLABLE_FIELDS = {
    "dti": 1.4,
    "fico": 0.5,
    "cltv": 2.1,
    "employment_type": 1.6,
    "property_type": 1.1,
    "occupancy": 0.7,
    "appraised_value": 0.9,
    "lock_extensions": 2.4,
    "title_policy_received_date": 1.3,
    "final_docs_received_date": 1.7,
    "investor_delivery_date": 1.1,
    "suspense_reason": 0.8,
}


def inject_missing_values(loans: pd.DataFrame) -> pd.DataFrame:
    """Non-random missingness, concentrated in the Wholesale channel.

    This is realistic: broker-submitted files pass through a third-party LOS
    and lose fields on the way in. It also means naive "drop nulls" analysis
    silently biases against Wholesale -- a good thing to be asked about.
    """
    r = C.rng(40)
    df = loans.copy()

    channel_rate = df.channel.map(
        {
            "Retail": C.NULL_RATE_RETAIL,
            "Wholesale": C.NULL_RATE_WHOLESALE,
            "Correspondent": C.NULL_RATE_CORRESPONDENT,
        }
    ).fillna(C.NULL_RATE_RETAIL).values

    for field, weight in NULLABLE_FIELDS.items():
        if field not in df.columns:
            continue
        p = np.clip(channel_rate * weight, 0, 0.35)
        mask = r.random(len(df)) < p
        # Never null a field on a row where it is already null.
        mask = mask & df[field].notna().values
        df.loc[mask, field] = np.nan if df[field].dtype.kind in "fiu" else None

    return df


def inject_duplicate_applications(loans: pd.DataFrame) -> pd.DataFrame:
    """Borrowers who reapply after a denial.

    The duplicate carries a new loan_id but nearly identical borrower and
    property attributes and an application date days later. Detecting these
    requires fuzzy matching, not a DISTINCT -- which is the point.
    """
    r = C.rng(41)
    df = loans.copy()

    denied = df[
        (df.status == "Fallout")
        & (df.fallout_reason.notna())
        & (df.fallout_reason.str.startswith("Denied"))
    ]
    if denied.empty:
        return df

    n = min(C.DUPLICATE_APPLICATION_COUNT, len(denied))
    picks = denied.sample(n=n, random_state=int(C.SEED % 2**31)).copy()

    max_id = int(df.loan_id.str.replace("ML-", "", regex=False).astype(int).max())
    picks["loan_id"] = [f"ML-{max_id + 1 + i}" for i in range(len(picks))]

    # Reapplied 20-75 days later, usually with a slightly repaired file.
    gap = r.integers(20, 76, len(picks))
    picks["application_date"] = picks.application_date + pd.to_timedelta(gap, unit="D")

    # Small credit improvements -- the borrower fixed something.
    picks["fico"] = np.clip(picks.fico.fillna(700) + r.integers(2, 26, len(picks)), 500, 850)
    picks["dti"] = np.clip(picks.dti.fillna(40) - r.uniform(0.5, 4.5, len(picks)), 10, 60).round(1)

    # Most reapplications succeed; some fail again.
    succeeded = r.random(len(picks)) < 0.62
    picks["status"] = np.where(succeeded, "Funded", "Fallout")
    picks["fallout_reason"] = np.where(succeeded, None, picks.fallout_reason)
    picks["is_reapplication"] = 1

    # Shift every downstream timestamp by the same gap so the record is coherent.
    date_cols = [
        c for c in picks.columns
        if picks[c].dtype.kind == "M" and c != "application_date"
    ]
    for c in date_cols:
        picks[c] = picks[c] + pd.to_timedelta(gap, unit="D")

    df["is_reapplication"] = 0
    out = pd.concat([df, picks], ignore_index=True)
    return out


def inject_dti_unit_quirk(loans: pd.DataFrame) -> pd.DataFrame:
    """One branch entering DTI as a decimal instead of a percentage.

    A classic units bug: 0.42 where 42 was meant. Confined to one branch over a
    five-month window, so it is findable by branch-level profiling and would
    silently corrupt any unguarded AVG(dti).
    """
    df = loans.copy()
    mask = (
        (df.branch_id == C.DTI_QUIRK_BRANCH)
        & (df.application_date >= pd.Timestamp(C.DTI_QUIRK_START))
        & (df.application_date <= pd.Timestamp(C.DTI_QUIRK_END))
        & df.dti.notna()
    )
    df.loc[mask, "dti"] = (df.loc[mask, "dti"] / 100.0).round(4)
    return df


def inject_out_of_sequence_timestamps(loans: pd.DataFrame) -> pd.DataFrame:
    """A handful of loans where a downstream timestamp precedes an upstream one.

    Cause in the real world: a manual back-dating correction in the LOS, or a
    system clock issue during a batch migration. Produces negative stage
    durations, so any duration calculation needs a guard -- which the build
    guide's calculated fields demonstrate.
    """
    r = C.rng(42)
    df = loans.copy()

    elig = df[
        df.underwriting_start.notna()
        & df.processing_end.notna()
        & df.appraisal_received.notna()
    ].index
    if len(elig) == 0:
        return df

    n = min(C.OUT_OF_SEQUENCE_COUNT, len(elig))
    picks = r.choice(elig, size=n, replace=False)

    # Underwriting cannot begin before BOTH processing is complete and the
    # appraisal is in. Back-date underwriting_start past that gate so the
    # anomaly is reliably visible as a negative stage duration -- otherwise
    # natural slack in the queue absorbs it and the artifact hides.
    gate = df.loc[picks, ["processing_end", "appraisal_received"]].max(axis=1)
    back = r.uniform(1.5, 5.0, n)
    df.loc[picks, "underwriting_start"] = gate - pd.to_timedelta(back, unit="D")
    df.loc[picks, "has_timestamp_anomaly"] = 1

    if "has_timestamp_anomaly" not in df.columns:
        df["has_timestamp_anomaly"] = 0
    df["has_timestamp_anomaly"] = df.has_timestamp_anomaly.fillna(0).astype(int)
    return df


def inject_trailing_whitespace_and_case(loans: pd.DataFrame) -> pd.DataFrame:
    """Inconsistent string formatting from multi-system data entry.

    Small but realistic: the same value arriving as "Retail", "retail ", and
    "RETAIL" depending on which system wrote the row. Forces a TRIM/UPPER in
    the Tableau calculated fields rather than a naive GROUP BY.
    """
    r = C.rng(43)
    df = loans.copy()

    for col in ["property_type", "occupancy"]:
        if col not in df.columns:
            continue
        vals = df[col].astype("object")
        notna = vals.notna().values
        u = r.random(len(df))

        trail = notna & (u < 0.012)
        vals.loc[trail] = vals.loc[trail].astype(str) + " "

        upper = notna & (u >= 0.012) & (u < 0.020)
        vals.loc[upper] = vals.loc[upper].astype(str).str.upper()

        df[col] = vals

    return df


def apply_all(loans: pd.DataFrame) -> pd.DataFrame:
    """Order matters: duplicates before nulls so the copies get their own
    independent missingness, and the DTI quirk before nulls so some quirked
    rows also end up null (as they would in reality)."""
    df = inject_duplicate_applications(loans)
    df = inject_dti_unit_quirk(df)
    df = inject_out_of_sequence_timestamps(df)
    df = inject_missing_values(df)
    df = inject_trailing_whitespace_and_case(df)
    return df.sort_values("application_date").reset_index(drop=True)
