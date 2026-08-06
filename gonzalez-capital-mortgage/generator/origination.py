"""Origination simulation: applications, congestion-driven cycle time, fallout.

The causal chain is the point of this module:

    rates -> application volume -> queue depth -> utilization -> cycle time
          -> fallout probability -> funded population

Cycle time is *computed* from congestion, never sampled from a distribution.
That is what makes the bottleneck migrate on its own: underwriting capacity is
partially elastic (overtime, contractors) while appraisal capacity is external
and fixed, so under load the constraint moves to appraisal without anyone
having to hard-code that outcome.
"""

import numpy as np
import pandas as pd

import config as C


# --------------------------------------------------------------- utilities
def congestion_multiplier(utilization: np.ndarray) -> np.ndarray:
    """Flat below the knee, then bends hard. Classic queueing behavior.

    Below ~80% utilization a queue absorbs variation. Past it, wait time grows
    superlinearly -- which is why a 2x volume increase produces ~3x wait.
    """
    u = np.asarray(utilization, dtype=float)
    excess = np.clip(u - C.CONGESTION_KNEE, 0, None)
    mult = 1.0 + (excess / (1 - C.CONGESTION_KNEE)) ** C.CONGESTION_EXPONENT
    return np.clip(mult, 1.0, C.CONGESTION_CEILING)


def _next_business_day(dates: pd.Series, holidays: set) -> pd.Series:
    """Push a date off weekends and holidays. Funding and closing do not happen
    on a Sunday, and the resulting gaps are visible in the data."""
    d = pd.to_datetime(dates).copy()
    for _ in range(6):
        bad = d.dt.weekday.isin([5, 6]) | d.isin(holidays)
        if not bad.any():
            break
        d = d.where(~bad, d + pd.Timedelta(days=1))
    return d


# ------------------------------------------------------------ volume model
def build_application_volume(daily_rate: pd.Series) -> pd.DataFrame:
    """Daily application counts driven by rate incentive plus seasonality.

    Refi demand is highly rate-elastic; purchase demand is seasonal and much
    less rate-sensitive. Their sum is what the operation actually has to absorb.
    """
    r = C.rng(10)
    window = pd.date_range(C.START_DATE, C.END_DATE, freq="D")
    rate = daily_rate.reindex(window)

    # Refi incentive measured against a trailing 12-month high -- the pool of
    # borrowers who could benefit from refinancing.
    trailing_high = rate.rolling(365, min_periods=1).max()
    incentive = (trailing_high - rate).clip(lower=0)

    # Refi response is a smooth convex function of incentive.
    refi_index = 0.10 + 2.55 * (incentive ** 1.55)

    # Purchase seasonality: spring/summer peak, deep December trough.
    doy = window.dayofyear.values
    seasonal = 1.0 + 0.30 * np.sin((doy - 78) / 365.0 * 2 * np.pi)
    purchase_index = 0.92 * seasonal

    # Rates also nudge purchase affordability, but far more weakly than refi.
    purchase_index = purchase_index * (1 + 0.22 * incentive.values)

    combined = refi_index.values + purchase_index

    # Weekday pattern: applications cluster Mon-Thu, collapse on weekends.
    weekday = window.weekday.values
    dow_factor = np.select(
        [weekday <= 3, weekday == 4, weekday == 5, weekday == 6],
        [1.13, 0.94, 0.24, 0.14],
    )
    holidays = set(pd.to_datetime(C.US_HOLIDAYS))
    holiday_factor = np.where(window.isin(holidays), 0.18, 1.0)

    raw = combined * dow_factor * holiday_factor
    raw = raw * r.lognormal(0, 0.115, len(raw))     # day-to-day noise

    # Scale so the total lands near the target application count.
    scaled = raw / raw.sum() * C.TARGET_APPLICATIONS
    counts = np.maximum(np.round(scaled).astype(int), 0)

    return pd.DataFrame(
        {
            "date": window,
            "applications": counts,
            "market_rate_30yr": rate.values,
            "refi_incentive": incentive.values,
        }
    )


# -------------------------------------------------------------- attributes
def _assign_geography(n, dim_geo, r):
    """Weight county selection by panel depth as a proxy for market size, so
    metro counties carry most volume -- but rural markets carry enough to
    matter (~22% of total, per the narrative)."""
    w = dim_geo.appraiser_panel_depth.values.astype(float)
    w = np.where(dim_geo.rural_flag.values == 1, w * 2.35, w)  # lift rural share
    w = w / w.sum()
    idx = r.choice(len(dim_geo), size=n, p=w)
    return dim_geo.iloc[idx].reset_index(drop=True)


def _purpose_mix(incentive: float) -> list:
    """Blend between the high-rate and low-rate purpose mixes.

    As incentive rises, refi share climbs at the expense of purchase -- the
    composition shift that accompanies every rate rally.
    """
    t = float(np.clip(incentive / 1.10, 0, 1))
    hi = np.array(C.PURPOSE_MIX_HIGH_RATE)
    lo = np.array(C.PURPOSE_MIX_LOW_RATE)
    mix = hi + t * (lo - hi)
    return (mix / mix.sum()).tolist()


def build_loan_attributes(volume: pd.DataFrame, dims: dict) -> pd.DataFrame:
    """One row per application with borrower/property/loan characteristics."""
    r = C.rng(11)
    dim_geo = dims["dim_geography"]
    dim_branch = dims["dim_branch"]
    dim_emp = dims["dim_employee"]

    app_dates = np.repeat(volume.date.values, volume.applications.values)
    incentives = np.repeat(volume.refi_incentive.values, volume.applications.values)
    mkt_rates = np.repeat(volume.market_rate_30yr.values, volume.applications.values)
    n = len(app_dates)

    df = pd.DataFrame(
        {
            "application_date": pd.to_datetime(app_dates),
            "_incentive": incentives,
            "_market_rate": mkt_rates,
        }
    )
    df["loan_id"] = [f"ML-{100000 + i}" for i in range(n)]

    # --- channel ---
    df["channel"] = r.choice(C.CHANNELS, size=n, p=C.CHANNEL_MIX)

    # --- geography, then branch within that state ---
    geo = _assign_geography(n, dim_geo, r)
    df["geo_id"] = geo.geo_id.values
    df["property_state"] = geo.state.values
    df["property_county"] = geo.county.values
    df["property_msa"] = geo.msa.values
    df["rural_flag"] = geo.rural_flag.values
    df["appraiser_panel_depth"] = geo.appraiser_panel_depth.values
    df["panel_capacity_index"] = geo.panel_capacity_index.values

    branch_by_state = {s: g.branch_id.tolist() for s, g in dim_branch.groupby("state")}
    all_branches = dim_branch.branch_id.tolist()
    df["branch_id"] = [
        r.choice(branch_by_state.get(s, all_branches)) for s in df.property_state
    ]
    df = df.merge(
        dim_branch[["branch_id", "branch_name", "region", "branch_size"]],
        on="branch_id",
        how="left",
    )

    # --- staff assignment within branch ---
    emp_by_branch_role = {
        (b, role): g.employee_id.tolist()
        for (b, role), g in dim_emp.groupby(["branch_id", "role"])
    }

    def pick(branch, role):
        pool = emp_by_branch_role.get((branch, role))
        if not pool:
            pool = dim_emp[dim_emp.role == role].employee_id.tolist()
        return r.choice(pool)

    df["loan_officer_id"] = [pick(b, "Loan Officer") for b in df.branch_id]
    df["processor_id"] = [pick(b, "Processor") for b in df.branch_id]
    df["underwriter_id"] = [pick(b, "Underwriter") for b in df.branch_id]
    df["closer_id"] = [pick(b, "Closer") for b in df.branch_id]

    # --- purpose depends on rate incentive at application time ---
    purposes = []
    for inc in df._incentive.values:
        purposes.append(r.choice(C.LOAN_PURPOSES, p=_purpose_mix(inc)))
    df["loan_purpose"] = purposes

    # Occupancy is drawn FIRST because it constrains which products are even
    # available: FHA/VA/USDA are owner-occupancy programs, so an investment
    # property cannot carry one.
    df["occupancy"] = r.choice(C.OCCUPANCY, size=n, p=C.OCCUPANCY_MIX)

    loan_types = r.choice(C.LOAN_TYPES, size=n, p=C.LOAN_TYPE_MIX)
    non_owner = df.occupancy.values != "Primary Residence"
    gov = np.isin(loan_types, C.GOVERNMENT_PRODUCTS)
    # Re-draw government products on non-owner-occupied into conventional/jumbo.
    needs_swap = non_owner & gov
    if needs_swap.any():
        loan_types[needs_swap] = r.choice(
            ["Conventional", "Jumbo"], size=needs_swap.sum(), p=[0.88, 0.12]
        )
    df["loan_type"] = loan_types

    # USDA does not permit cash-out refinance -- reassign those to rate-term.
    usda_cashout = (df.loan_type == "USDA") & (df.loan_purpose == "Cash-Out Refi")
    df.loc[usda_cashout, "loan_purpose"] = "Rate-Term Refi"

    df["property_type"] = r.choice(C.PROPERTY_TYPES, size=n, p=C.PROPERTY_TYPE_MIX)
    df["employment_type"] = r.choice(C.EMPLOYMENT_TYPES, size=n, p=C.EMPLOYMENT_MIX)

    # --- credit profile ---
    # FICO skews high for conventional/jumbo, lower for FHA. Correlated with
    # DTI and LTV so the credit box behaves sensibly.
    fico_base = np.select(
        [
            df.loan_type == "Jumbo",
            df.loan_type == "Conventional",
            df.loan_type == "VA",
            df.loan_type == "FHA",
            df.loan_type == "USDA",
        ],
        [762, 741, 718, 679, 701],
        default=730,
    )
    fico = np.round(fico_base + r.normal(0, 38, n)).astype(int)
    # Enforce the product's minimum credit score -- a 590 FICO conventional
    # loan would not have been approved.
    fico_floor = df.loan_type.map(C.MIN_FICO).fillna(620).values
    df["fico"] = np.clip(fico, fico_floor, 840).astype(int)

    # --- LTV, subject to product / purpose / occupancy caps ---
    # Purchase LTV clusters at the familiar down-payment points; refis are
    # more dispersed because they reflect accumulated equity.
    ltv = np.where(
        df.loan_purpose == "Purchase",
        r.choice([80.0, 90.0, 95.0, 96.5, 75.0], size=n, p=[0.34, 0.14, 0.19, 0.23, 0.10]),
        np.clip(r.normal(68, 13, n), 25, 95),
    )
    # VA borrowers commonly put nothing down.
    ltv = np.where(df.loan_type == "VA", np.clip(ltv + 8, 25, 100), ltv)

    # Apply the binding cap: product limit for the transaction purpose, then
    # the occupancy overlay on top.
    cap_map = {
        "Purchase": C.MAX_LTV_PURCHASE,
        "Rate-Term Refi": C.MAX_LTV_RATE_TERM,
        "Cash-Out Refi": C.MAX_LTV_CASHOUT,
    }
    product_cap = np.array(
        [cap_map[p].get(t, 97.0) for p, t in zip(df.loan_purpose, df.loan_type)]
    )
    occ_cap = np.select(
        [df.occupancy.values == "Investment", df.occupancy.values == "Second Home"],
        [C.MAX_LTV_INVESTMENT, C.MAX_LTV_SECOND_HOME],
        default=100.0,
    )
    binding_cap = np.minimum(product_cap, occ_cap)

    # Where the draw exceeds the cap, pull it to just under rather than
    # piling every violator exactly on the limit.
    over = ltv > binding_cap
    ltv = np.where(over, binding_cap - r.uniform(0, 6.5, n), ltv)
    df["ltv"] = np.round(np.clip(ltv, 20, 100), 1)

    # CLTV >= LTV; a second lien only exists where the first leaves room.
    second_lien = (r.random(n) < 0.14) & (df.ltv.values < 80)
    cltv_add = np.where(second_lien, r.choice([5, 10, 15, 20], size=n), 0)
    df["cltv"] = np.round(np.clip(df.ltv + cltv_add, 20, 100), 1)

    # DTI inversely related to FICO, with self-employed running higher.
    dti = 34 + (760 - df.fico) * 0.030 + r.normal(0, 5.4, n)
    dti = dti + np.where(df.employment_type == "Self-Employed", 2.9, 0)
    df["dti"] = np.round(np.clip(dti, 12, 57), 1)

    # --- loan amount: scaled by market, capped by type ---
    metro_factor = np.where(df.rural_flag == 1, 0.71, 1.0)
    state_factor = df.property_state.map(
        {"TX": 1.00, "AZ": 1.09, "CO": 1.27, "NC": 1.02, "UT": 1.21,
         "NV": 1.08, "OK": 0.83, "MT": 1.06, "WY": 0.92, "NM": 0.88,
         "KS": 0.81, "NE": 0.85}
    ).fillna(1.0).values

    base_amt = r.lognormal(np.log(322_000), 0.42, n) * metro_factor * state_factor

    # Each product occupies its own balance range. A "jumbo" below the
    # conforming limit is a contradiction in terms, and an FHA loan above the
    # FHA ceiling could not be insured.
    lt = df.loan_type.values
    amt = base_amt.copy()

    # Jumbo: must exceed the conforming limit. Scale up, then floor it just
    # above the limit so the product name is always accurate.
    jumbo = lt == "Jumbo"
    amt = np.where(jumbo, base_amt * 2.45, amt)
    # Floor with enough headroom that the later round-to-hundreds cannot push
    # a jumbo back below the limit.
    amt = np.where(
        jumbo & (amt <= C.CONFORMING_LIMIT + 1_000),
        C.CONFORMING_LIMIT + r.uniform(5_000, 180_000, n),
        amt,
    )

    # Conventional: conforming, so it must stay at or below the limit.
    conv = lt == "Conventional"
    amt = np.where(conv, np.minimum(amt, C.CONFORMING_LIMIT), amt)

    # FHA / USDA / VA: capped at their program ceilings.
    amt = np.where(lt == "FHA", np.minimum(amt, C.FHA_LIMIT_STANDARD), amt)
    amt = np.where(lt == "USDA", np.minimum(amt * 0.74, C.USDA_PRACTICAL_LIMIT), amt)
    amt = np.where(lt == "VA", np.minimum(amt, C.VA_PRACTICAL_LIMIT), amt)

    df["loan_amount"] = np.round(np.clip(amt, 65_000, 2_400_000), -2)

    df["appraised_value"] = np.round(df.loan_amount / (df.ltv / 100.0), -2)

    # --- note rate: market rate plus risk-based adjustments ---
    note = df._market_rate.values.copy()
    note += np.where(df.fico < 660, 0.62, np.where(df.fico < 700, 0.34,
                     np.where(df.fico < 740, 0.14, 0.0)))
    note += np.where(df.ltv > 90, 0.22, 0.0)
    note += np.where(df.occupancy == "Investment", 0.71, 0.0)
    note += np.where(df.occupancy == "Second Home", 0.34, 0.0)
    # Government products price BELOW conventional -- the guaranty lets the
    # lender accept a lower note rate for the same credit profile.
    note += np.where(df.loan_type == "VA", -0.28, 0.0)
    note += np.where(df.loan_type == "FHA", -0.18, 0.0)
    note += np.where(df.loan_type == "USDA", -0.22, 0.0)
    note += np.where(df.loan_type == "Jumbo", 0.12, 0.0)
    note += np.where(df.loan_purpose == "Cash-Out Refi", 0.28, 0.0)
    note += r.normal(0, 0.11, n)
    df["note_rate"] = np.round(np.clip(note, 3.5, 10.5), 3)

    # --- rate lock ---
    lock_offset = r.integers(0, 6, n)
    df["lock_date"] = df.application_date + pd.to_timedelta(lock_offset, unit="D")
    lock_term = r.choice([30, 45, 60], size=n, p=[0.36, 0.44, 0.20])
    df["lock_term_days"] = lock_term
    df["lock_expiration"] = df.lock_date + pd.to_timedelta(lock_term, unit="D")

    return df.drop(columns=["_market_rate"])


# ---------------------------------------------------- stage timing engine
def simulate_stages(df: pd.DataFrame, dims: dict) -> pd.DataFrame:
    """Walk each loan through the pipeline, deriving durations from congestion.

    Processed in application-date order so queue depth at each stage reflects
    work already in flight -- the feedback loop that produces the surge.
    """
    r = C.rng(12)
    holidays = set(pd.to_datetime(C.US_HOLIDAYS))
    df = df.sort_values("application_date").reset_index(drop=True)
    n = len(df)

    dim_emp = dims["dim_employee"].set_index("employee_id")
    uw_tier = dim_emp.experience_tier.to_dict()
    uw_cap = dim_emp.capacity_index.to_dict()

    # ---- daily arrival counts drive utilization at each stage ----
    daily_apps = df.groupby(df.application_date.dt.normalize()).size()
    all_days = pd.date_range(C.START_DATE, C.END_DATE, freq="D")
    daily_apps = daily_apps.reindex(all_days, fill_value=0)

    # Smooth arrivals into a demand signal (work arrives faster than it clears).
    arrivals = daily_apps.rolling(14, min_periods=1).mean()

    # A real pipeline carries a BACKLOG: work that arrived during the spike is
    # still in the system months later, so congestion persists well after
    # arrivals moderate. Model it as an exponentially-weighted accumulation
    # with a long memory -- this is what turns a two-month application spike
    # into a two-quarter operational problem, which is what actually happened
    # in every rate rally.
    backlog = arrivals.ewm(halflife=52, min_periods=1).mean()

    # Effective demand blends current arrivals with the persistent backlog.
    demand = 0.38 * arrivals + 0.62 * backlog

    # ---- capacity by day ----
    # Capacity is calibrated against PRE-SHOCK demand so that baseline
    # utilization sits below the congestion knee. The surge then drives
    # utilization past it, and cycle time responds nonlinearly.
    preshock_demand = demand.loc[: pd.Timestamp(C.PRE_SHOCK_END)].mean()
    preshock_demand = max(float(preshock_demand), 1e-9)

    base_uw = preshock_demand / C.PRESHOCK_UW_UTILIZATION
    base_proc = preshock_demand / C.PRESHOCK_PROCESSING_UTILIZATION
    base_appr = preshock_demand / C.PRESHOCK_APPRAISAL_UTILIZATION

    # Underwriting and processing flex with overtime/contractors, lagged ~4
    # weeks -- staffing responds to a surge only after it is visible.
    surge_signal = (demand / preshock_demand).clip(lower=1.0)
    lagged_surge = surge_signal.shift(28).fillna(1.0)

    uw_capacity = base_uw * (1 + C.UW_SURGE_ELASTICITY * (lagged_surge - 1))
    proc_capacity = base_proc * (1 + C.PROCESSING_ELASTICITY * (lagged_surge - 1))

    uw_util = (demand / uw_capacity).clip(0, 3.0)
    proc_util = (demand / proc_capacity).clip(0, 3.0)

    # Appraisal capacity is EXTERNAL and effectively fixed. No elasticity term.
    # This asymmetry is what moves the bottleneck.
    appr_util = (demand / base_appr).clip(0, 3.5)

    uw_mult = pd.Series(congestion_multiplier(uw_util.values), index=all_days)
    proc_mult = pd.Series(congestion_multiplier(proc_util.values), index=all_days)
    appr_mult = pd.Series(congestion_multiplier(appr_util.values), index=all_days)

    def lookup(mult_series, dates):
        """Read congestion at the date a loan actually REACHES a stage, not at
        application. A loan applying pre-shock can still hit underwriting in the
        middle of the surge, and it should feel that congestion."""
        d = pd.to_datetime(pd.Series(dates)).dt.normalize()
        d = d.clip(lower=all_days[0], upper=all_days[-1])
        return d.map(mult_series).fillna(1.0).values

    app_day = df.application_date.dt.normalize()
    proc_m = lookup(proc_mult, app_day)   # provisional; refined per-stage below
    uw_m = lookup(uw_mult, app_day)
    appr_m = lookup(appr_mult, app_day)

    # ---------------------------------------------------------- stage 1: docs
    doc_days = C.BASE_DOC_COLLECTION * r.lognormal(0, 0.42, n)
    # Self-employed borrowers take materially longer to produce documentation.
    doc_days *= np.where(df.employment_type == "Self-Employed", 1.62, 1.0)
    doc_days *= np.where(df.employment_type == "Mixed", 1.31, 1.0)
    # Wholesale docs arrive via a broker -- slower and less complete.
    doc_days *= np.where(df.channel == "Wholesale", 1.24, 1.0)
    df["docs_received"] = df.application_date + pd.to_timedelta(
        np.round(doc_days, 2), unit="D"
    )

    # ------------------------------------------------- stage 2: processing
    # Congestion is read at the moment the file arrives at this stage.
    proc_m = lookup(proc_mult, df.docs_received)
    proc_wait = 0.9 * proc_m * r.lognormal(0, 0.34, n)
    df["processing_start"] = df.docs_received + pd.to_timedelta(
        np.round(proc_wait, 2), unit="D"
    )
    proc_touch = C.BASE_PROCESSING_TOUCH * (1 + 0.30 * (proc_m - 1)) * r.lognormal(0, 0.30, n)
    df["processing_end"] = df.processing_start + pd.to_timedelta(
        np.round(proc_touch, 2), unit="D"
    )

    # ------------------------------------------------- stage 3: appraisal
    # Ordered early, in parallel with processing -- so appraisal wait only
    # becomes the binding constraint when it exceeds everything else.
    order_lag = r.uniform(0.5, 2.5, n)
    df["appraisal_ordered"] = df.docs_received + pd.to_timedelta(
        np.round(order_lag, 2), unit="D"
    )

    # Vendor assignment by state coverage.
    vendor_map = {}
    for vid, _name, states, _sla in C.VENDORS:
        if states is not None:
            for s in states:
                vendor_map.setdefault(s, []).append(vid)
    national = [v[0] for v in C.VENDORS if v[2] is None]

    def pick_vendor(state):
        pool = vendor_map.get(state, []) + national
        # Regional AMC preferred ~72% of the time.
        if len(pool) > 1 and r.random() < 0.72:
            return pool[0]
        return r.choice(pool)

    df["amc_vendor_id"] = [pick_vendor(s) for s in df.property_state]

    # Congestion at the moment the appraisal is ordered.
    appr_m = lookup(appr_mult, df.appraisal_ordered)

    base_appr = np.where(
        df.rural_flag == 1, C.BASE_APPRAISAL_WAIT_RURAL, C.BASE_APPRAISAL_WAIT_METRO
    ).astype(float)

    # Thin panels amplify congestion, but only under load. At baseline a rural
    # county with 4 appraisers serves its 4 orders fine; it is the surge that
    # exposes the shallow bench. So the panel term scales with excess
    # congestion rather than applying as a flat multiplier.
    panel_thinness = np.clip(
        1.0 / np.maximum(df.panel_capacity_index.values, 0.25), 1.0, 3.0
    )
    congestion_excess = np.clip(appr_m - 1, 0, None) / max(C.CONGESTION_CEILING - 1, 1e-9)

    # Rural markets get deprioritized by the AMC when volume spikes -- the
    # single mechanism behind the geographic finding.
    rural_amplifier = np.where(
        df.rural_flag == 1,
        1.0 + (C.RURAL_SURGE_PENALTY - 1.0) * congestion_excess
        + 0.32 * (panel_thinness - 1.0) * congestion_excess,
        1.0,
    )

    # Appraisal responds to congestion sublinearly -- the panel does absorb
    # some surge via overtime before it saturates. Metro panels are deep enough
    # to absorb more of it, which is what concentrates the pain in rural
    # markets rather than spreading it evenly.
    appr_exponent = np.where(df.rural_flag == 1, 0.62, 0.58)
    appr_wait = base_appr * (appr_m ** appr_exponent) * rural_amplifier * r.lognormal(0, 0.28, n)

    # Vendor outage: a three-week stall for orders routed to one AMC.
    outage_start = pd.Timestamp(C.VENDOR_OUTAGE_START)
    outage_end = outage_start + pd.Timedelta(days=C.VENDOR_OUTAGE_DAYS)
    in_outage = (
        (df.amc_vendor_id == C.VENDOR_OUTAGE_ID)
        & (df.appraisal_ordered >= outage_start)
        & (df.appraisal_ordered < outage_end)
    ).values
    appr_wait = appr_wait + np.where(in_outage, r.uniform(9, 19, n), 0)

    df["appraisal_received"] = df.appraisal_ordered + pd.to_timedelta(
        np.round(appr_wait, 2), unit="D"
    )
    df["_appraisal_wait_days"] = np.round(appr_wait, 2)

    # ---------------------------------------------- stage 4: underwriting
    # UW cannot start until BOTH processing is done and the appraisal is in.
    uw_ready = np.maximum(
        df.processing_end.values.astype("datetime64[ns]"),
        df.appraisal_received.values.astype("datetime64[ns]"),
    )
    # Congestion at the moment the file lands in the underwriting queue.
    uw_m = lookup(uw_mult, pd.to_datetime(uw_ready))
    uw_queue_wait = 1.1 * uw_m * r.lognormal(0, 0.38, n)
    df["underwriting_start"] = pd.to_datetime(uw_ready) + pd.to_timedelta(
        np.round(uw_queue_wait, 2), unit="D"
    )

    # Touch time flexes far less than wait time -- underwriters work faster
    # under pressure, they just cannot work proportionally faster.
    cap_idx = df.underwriter_id.map(uw_cap).fillna(1.0).values
    # Touch time stretches under load -- more files in flight means more
    # context-switching and more re-reviews -- but far less than wait time.
    # This is the crux of the story: underwriting DID slow, just not enough to
    # explain the blowout.
    uw_touch = (
        C.BASE_UW_TOUCH
        * (1 + 0.78 * (uw_m - 1))
        / cap_idx
        * r.lognormal(0, 0.26, n)
    )
    # Complex files take longer regardless of congestion.
    uw_touch *= np.where(df.employment_type == "Self-Employed", 1.28, 1.0)
    uw_touch *= np.where(df.loan_type == "Jumbo", 1.22, 1.0)
    df["underwriting_end"] = df.underwriting_start + pd.to_timedelta(
        np.round(uw_touch, 2), unit="D"
    )
    df["conditional_approval_date"] = df.underwriting_end

    df["_uw_touch_days"] = np.round(uw_touch, 2)
    df["_uw_wait_days"] = np.round(uw_queue_wait, 2)
    df["_proc_touch_days"] = np.round(proc_touch, 2)
    df["_proc_wait_days"] = np.round(proc_wait, 2)
    df["_doc_days"] = np.round(doc_days, 2)

    # ------------------------------------------ stage 5: condition clearing
    # THE REWORK LOOP. Rounds rise under load because rushed files get sloppier
    # reviews and incomplete document collection -- not because anyone is idle.
    tier_penalty = df.underwriter_id.map(uw_tier).map(
        {"Junior": 0.55, "Mid": 0.16, "Senior": 0.0}
    ).fillna(0.2).values

    # Rework is driven by congestion at BOTH ends: a rushed underwriter issues
    # sloppier condition sets, and a swamped processor collects documents less
    # completely up front. Normalized so the congestion term dominates.
    # Use the system-wide congestion the file actually lived through (peak of
    # processing and underwriting pressure), not just the UW queue at arrival --
    # rework originates upstream, at intake, well before underwriting sees it.
    system_cong = np.maximum(proc_m, uw_m)
    cong = np.clip(system_cong - 1, 0, None) / max(C.CONGESTION_CEILING - 1, 1e-9)
    proc_cong = np.clip(proc_m - 1, 0, None) / max(C.CONGESTION_CEILING - 1, 1e-9)

    cond_lambda = (
        0.40                                          # baseline extra rounds
        + 3.60 * cong                                 # rushed underwriting
        + 0.70 * proc_cong                            # incomplete doc intake
        + tier_penalty
        + np.where(df.employment_type == "Self-Employed", 0.48, 0)
        + np.where(df.employment_type == "Mixed", 0.26, 0)
        + np.where(df.channel == "Wholesale", 0.34, 0)
        + np.where(df.fico < 680, 0.29, 0)
        + np.where(df.ltv > 90, 0.14, 0)
    )
    rounds = 1 + r.poisson(np.clip(cond_lambda, 0.05, None), n)
    df["condition_rounds"] = np.clip(rounds, 1, 9)

    # Each round costs borrower response time, which also stretches under load
    # (borrowers get less attentive service when everyone is busy).
    per_round = C.BASE_CONDITION_ROUND * (1 + 0.18 * (uw_m - 1)) * r.lognormal(0, 0.34, n)
    cond_days = df.condition_rounds.values * per_round
    df["final_conditions_cleared"] = df.conditional_approval_date + pd.to_timedelta(
        np.round(cond_days, 2), unit="D"
    )
    df["_condition_days"] = np.round(cond_days, 2)

    # ------------------------------------------------------ stage 6: CTC
    ctc_lag = 0.8 * r.lognormal(0, 0.30, n)
    df["ctc_date"] = df.final_conditions_cleared + pd.to_timedelta(
        np.round(ctc_lag, 2), unit="D"
    )

    close_lag = C.BASE_CTC_TO_FUNDING * (1 + 0.12 * (uw_m - 1)) * r.lognormal(0, 0.26, n)
    df["closing_scheduled"] = df.ctc_date + pd.to_timedelta(np.round(close_lag, 2), unit="D")
    df["closing_scheduled"] = _next_business_day(df.closing_scheduled, holidays)

    fund_lag = r.choice([0, 1, 2, 3], size=n, p=[0.30, 0.44, 0.19, 0.07])
    df["funded_date"] = df.closing_scheduled + pd.to_timedelta(fund_lag, unit="D")
    df["funded_date"] = _next_business_day(df.funded_date, holidays)
    df["_ctc_to_fund_days"] = np.round(close_lag, 2)

    return df


# ------------------------------------------------------------- fallout
def apply_fallout(df: pd.DataFrame) -> pd.DataFrame:
    """Fallout probability rises with elapsed cycle time.

    This is the mechanism that turns a cycle-time problem into a revenue
    problem: a borrower quoted 30 days who is still waiting at day 50 goes
    shopping. Loans that fall out are truncated at whatever stage they reached.
    """
    r = C.rng(13)
    n = len(df)

    total_days = (df.funded_date - df.application_date).dt.total_seconds() / 86400.0
    excess = np.clip(total_days - 30, 0, None)

    p = C.FALLOUT_BASE + C.FALLOUT_DAYS_SENSITIVITY * excess
    # Refis fall out more readily -- there is no purchase contract forcing the
    # borrower to close.
    p = p * np.where(df.loan_purpose == "Purchase", 0.68, 1.30)
    p = p * np.where(df.channel == "Wholesale", 1.16, 1.0)
    # Weak credit files fail underwriting on the merits, independent of timing.
    p = p + np.where(df.fico < 640, 0.10, 0) + np.where(df.dti > 50, 0.075, 0)
    p = np.clip(p, 0, C.FALLOUT_CAP)

    fell_out = r.random(n) < p
    df["status"] = np.where(fell_out, "Fallout", "Funded")

    # Assign a stage of death and a reason, weighted toward whatever was slow.
    reason = np.full(n, "", dtype=object)
    stage = np.full(n, "", dtype=object)

    is_credit = (df.fico.values < 640) | (df.dti.values > 50)
    timing_driven = excess.values > 12

    for i in np.where(fell_out)[0]:
        if is_credit[i] and r.random() < 0.62:
            reason[i] = r.choice(
                ["Denied - Credit", "Denied - DTI", "Denied - Insufficient Assets"],
                p=[0.42, 0.40, 0.18],
            )
            stage[i] = "Underwriting"
        elif timing_driven[i] and r.random() < 0.74:
            reason[i] = r.choice(
                ["Borrower Withdrew - Rate Shopping", "Borrower Withdrew - Timeline"],
                p=[0.58, 0.42],
            )
            stage[i] = r.choice(
                ["Appraisal", "Condition Clearing", "Underwriting"], p=[0.41, 0.44, 0.15]
            )
        else:
            reason[i] = r.choice(C.FALLOUT_REASONS)
            stage[i] = r.choice(
                ["Processing", "Appraisal", "Underwriting", "Condition Clearing"],
                p=[0.18, 0.26, 0.32, 0.24],
            )

    df["fallout_reason"] = np.where(fell_out, reason, None)
    df["fallout_stage"] = np.where(fell_out, stage, None)

    # Truncate timestamps past the stage where the loan died.
    order = {
        "Processing": ["appraisal_ordered", "appraisal_received", "underwriting_start",
                       "underwriting_end", "conditional_approval_date",
                       "final_conditions_cleared", "ctc_date", "closing_scheduled",
                       "funded_date"],
        "Appraisal": ["underwriting_start", "underwriting_end",
                      "conditional_approval_date", "final_conditions_cleared",
                      "ctc_date", "closing_scheduled", "funded_date"],
        "Underwriting": ["conditional_approval_date", "final_conditions_cleared",
                         "ctc_date", "closing_scheduled", "funded_date"],
        "Condition Clearing": ["ctc_date", "closing_scheduled", "funded_date"],
    }
    for st, cols in order.items():
        mask = (df.status == "Fallout") & (df.fallout_stage == st)
        for c in cols:
            df.loc[mask, c] = pd.NaT

    # A fallout at the appraisal stage still has no condition rounds recorded.
    df.loc[(df.status == "Fallout") & df.conditional_approval_date.isna(),
           "condition_rounds"] = 0

    # Loans still in flight at the end of the window are "In Process", not
    # funded -- a real pipeline always has open files at the reporting date.
    cutoff = pd.Timestamp(C.END_DATE)
    in_flight = (df.status == "Funded") & (df.funded_date > cutoff)
    df.loc[in_flight, "status"] = "In Process"
    for c in ["funded_date", "closing_scheduled"]:
        df.loc[in_flight, c] = pd.NaT
    df.loc[in_flight & (df.ctc_date > cutoff), "ctc_date"] = pd.NaT
    df.loc[in_flight & (df.final_conditions_cleared > cutoff),
           "final_conditions_cleared"] = pd.NaT

    return df


def build(dims: dict, daily_rate: pd.Series):
    volume = build_application_volume(daily_rate)
    df = build_loan_attributes(volume, dims)
    df = simulate_stages(df, dims)
    df = apply_fallout(df)
    return df, volume
