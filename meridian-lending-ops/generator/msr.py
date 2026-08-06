"""Monthly MSR panel: prepayment, credit migration, servicing cost, valuation.

Grain: one row per retained loan per month, from funding through payoff or the
reporting cutoff.

Four forces drive value, and they are modeled separately so the dashboard can
decompose value change into its causes rather than just plotting a line:

  1. Rate incentive -> prepayment (S-curve with burnout) -> expected life
  2. Delinquency migration -> servicing cost + advance obligations
  3. Servicing cost drift -> direct subtraction from the cash-flow stream
  4. Short rate -> float income on escrow balances

Force 4 is why rising rates help MSR twice over: slower prepay AND more float.
"""

import numpy as np
import pandas as pd

import config as C


# ------------------------------------------------------------- prepayment
def prepay_cpr(incentive, seasoning_months, burnout_factor, is_investor=False):
    """Annualized CPR from refinance incentive.

    S-curve: flat at the turnover floor when out of the money, steep through
    the middle, saturating at the ceiling. Burnout damps the response for
    borrowers who repeatedly failed to act on an incentive.
    """
    inc = np.asarray(incentive, dtype=float)

    # Logistic in incentive space.
    s = 1.0 / (1.0 + np.exp(-C.CPR_STEEPNESS * (inc - C.CPR_MIDPOINT)))
    cpr = C.CPR_FLOOR + (C.CPR_CEILING - C.CPR_FLOOR) * s

    # Seasoning ramp: new loans prepay slowly regardless of incentive (there is
    # a practical floor on how fast a borrower can refinance again).
    ramp = np.clip(np.asarray(seasoning_months, dtype=float) / 9.0, 0.12, 1.0)
    cpr = C.CPR_FLOOR + (cpr - C.CPR_FLOOR) * ramp

    # Burnout: damps only the incentive-driven portion, never the floor.
    cpr = C.CPR_FLOOR + (cpr - C.CPR_FLOOR) * np.asarray(burnout_factor, dtype=float)

    # Investors refinance less readily.
    if is_investor is not False:
        cpr = np.where(is_investor, cpr * 0.82, cpr)

    return np.clip(cpr, 0.005, 0.90)


def cpr_to_smm(cpr):
    """Annual CPR -> single monthly mortality."""
    return 1.0 - (1.0 - np.asarray(cpr, dtype=float)) ** (1.0 / 12.0)


# --------------------------------------------------------- credit migration
def _build_transition_matrix(fico, ltv, loan_type, occupancy, seasoning):
    """Per-loan monthly roll rates, scaled off the prime baseline.

    Credit risk multiplier keys off FICO band, LTV, product, and occupancy;
    the seasoning curve peaks around months 18-30, which reproduces the
    familiar vintage default hump.
    """
    risk = np.ones(len(fico))
    risk *= np.select(
        [fico < 620, fico < 660, fico < 700, fico < 740, fico < 780],
        [3.30, 2.35, 1.62, 1.10, 0.78],
        default=0.55,
    )
    risk *= np.where(ltv > 95, 1.55, np.where(ltv > 90, 1.32, np.where(ltv > 80, 1.12, 0.92)))
    risk *= np.select(
        [loan_type == "FHA", loan_type == "USDA", loan_type == "VA", loan_type == "Jumbo"],
        [1.85, 1.55, 1.25, 0.62],
        default=1.0,
    )
    risk *= np.where(occupancy == "Investment", 1.28, np.where(occupancy == "Second Home", 1.12, 1.0))

    # Seasoning hazard: near zero at origination, peaks ~month 24, then decays.
    s = np.asarray(seasoning, dtype=float)
    season_mult = 1.65 * np.exp(-((s - 24.0) ** 2) / (2 * 14.0 ** 2))
    season_mult = np.clip(season_mult, 0.06, 1.75)

    # Cap the compounded multiplier: a weak file is riskier, not unboundedly so.
    return np.clip(risk * season_mult, 0.03, C.MAX_CREDIT_RISK_MULTIPLIER)


def migrate_delinquency(current_status, risk_mult, rng):
    """Advance each loan one month through the roll-rate matrix."""
    n = len(current_status)
    new_status = np.array(current_status, dtype=object)
    u = rng.random(n)

    for status in C.DQ_STATUSES:
        mask = current_status == status
        if not mask.any():
            continue
        transitions = C.ROLL_RATES.get(status, {status: 1.0})

        dest = list(transitions.keys())
        probs = np.array([transitions[d] for d in dest], dtype=float)

        # Scale the deterioration probability by the loan's risk multiplier;
        # the remainder flows back to the best available (cure) state.
        worse = {
            "Current": ["30 DPD"],
            "30 DPD": ["60 DPD"],
            "60 DPD": ["90+ DPD"],
            "90+ DPD": ["Foreclosure"],
            "Foreclosure": ["REO"],
            "REO": [],
            "Liquidated": [],
        }[status]

        idx_sub = np.where(mask)[0]
        rm = risk_mult[idx_sub]

        adj = np.tile(probs, (len(idx_sub), 1)).astype(float)
        for j, d in enumerate(dest):
            if d in worse:
                adj[:, j] = np.clip(probs[j] * rm, 0, 0.95)

        # Renormalize onto the cure state so rows still sum to 1.
        cure_idx = [j for j, d in enumerate(dest) if d not in worse]
        if cure_idx:
            other = adj[:, [j for j in range(len(dest)) if j not in cure_idx]].sum(axis=1)
            remaining = np.clip(1.0 - other, 0.001, None)
            cure_weights = probs[cure_idx] / max(probs[cure_idx].sum(), 1e-9)
            for k, j in enumerate(cure_idx):
                adj[:, j] = remaining * cure_weights[k]

        adj = adj / adj.sum(axis=1, keepdims=True)
        cum = adj.cumsum(axis=1)
        picks = (u[idx_sub][:, None] > cum).sum(axis=1)
        picks = np.clip(picks, 0, len(dest) - 1)
        new_status[idx_sub] = np.array(dest, dtype=object)[picks]

    return new_status


# ------------------------------------------------------------- valuation
def msr_multiple(note_rate, market_rate, cpr, dq_status, servicing_cost, short_rate, escrow_flag):
    """MSR value as a multiple of UPB.

    Economics: the multiple is the present value of the servicing strip, so it
    rises with expected life (low prepay), falls with cost, falls hard with
    delinquency, and rises with float income.
    """
    # Expected life falls as CPR rises. WAL ~ 1/CPR bounded to a sane range.
    expected_life = np.clip(1.0 / np.maximum(cpr, 0.02), 1.2, 14.0)
    # Dampened with a square root: MSR value is sensitive to expected life, but
    # not linearly -- the near-term servicing strip is collected regardless of
    # what happens in later years.
    life_factor = np.clip(np.sqrt(expected_life / 7.0), 0.42, 1.32)

    base = C.MSR_MULTIPLE_BASE * life_factor

    # Delinquency haircut -- steep, because a nonperforming loan costs more to
    # service, triggers advances, and risks losing the servicing entirely.
    dq_haircut = pd.Series(dq_status).map(
        {
            "Current": 1.0,
            "30 DPD": 0.88,
            "60 DPD": 0.68,
            "90+ DPD": 0.42,
            "Foreclosure": 0.20,
            "REO": 0.06,
            "Liquidated": 0.0,
        }
    ).fillna(1.0).values
    base = base * dq_haircut

    # Servicing cost is a direct subtraction, expressed against a normalized
    # cost level so the sensitivity is visible but not overwhelming.
    cost_drag = np.clip(servicing_cost / C.SERVICING_COST_BASE, 0.5, 6.0)
    base = base * np.clip(1.18 - 0.18 * cost_drag, 0.35, 1.12)

    # Float income lifts value when short rates are high -- and only matters
    # where there is an escrow account to earn float on.
    float_lift = 1.0 + 0.055 * np.asarray(short_rate, dtype=float) * np.where(
        np.asarray(escrow_flag, dtype=float) > 0, 1.0, 0.35
    )
    base = base * float_lift

    return np.clip(base, C.MSR_MULTIPLE_FLOOR, C.MSR_MULTIPLE_CEILING)


def _monthly_payment(principal, annual_rate, term_months=360):
    r = np.asarray(annual_rate, dtype=float) / 100.0 / 12.0
    p = np.asarray(principal, dtype=float)
    r = np.where(r <= 0, 1e-6, r)
    return p * r / (1.0 - (1.0 + r) ** (-term_months))


def build_msr_panel(loans: pd.DataFrame, dim_rates: pd.DataFrame) -> pd.DataFrame:
    """Walk the retained servicing portfolio month by month."""
    rng = C.rng(30)

    port = loans[
        (loans.status == "Funded") & (loans.servicing_status == "Retained")
    ].copy()
    port = port[port.funded_date.notna()]

    # Monthly market rate and short rate series.
    months = pd.date_range(
        pd.Timestamp(C.START_DATE).to_period("M").to_timestamp(),
        pd.Timestamp("2026-06-30").to_period("M").to_timestamp(),
        freq="MS",
    )
    mkt = dim_rates.set_index("week_start")[["market_rate_30yr", "short_rate"]]
    mkt_m = mkt.resample("MS").mean().reindex(months).ffill().bfill()

    # --- initialize loan state ---
    n = len(port)
    loan_id = port.loan_id.values
    note_rate = port.note_rate.values.astype(float)
    orig_upb = port.loan_amount.values.astype(float)
    fico = port.fico.values
    ltv = port.ltv.values.astype(float)
    ltype = port.loan_type.values
    occ = port.occupancy.values
    escrow = pd.to_numeric(port.escrow_flag, errors="coerce").fillna(0).values.astype(float)
    fund_month = port.funded_date.dt.to_period("M").dt.to_timestamp().values
    is_investor = (port.occupancy == "Investment").values

    pmt = _monthly_payment(orig_upb, note_rate)

    upb = orig_upb.copy()
    status = np.array(["Current"] * n, dtype=object)
    burnout = np.ones(n)
    active = np.zeros(n, dtype=bool)
    paid_off = np.zeros(n, dtype=bool)
    payoff_month = np.full(n, np.datetime64("NaT"), dtype="datetime64[ns]")
    months_dq = np.zeros(n, dtype=int)
    escrow_adv = np.zeros(n)

    rows = []

    for m in months:
        # Activate loans that funded this month or earlier.
        newly = (fund_month <= np.datetime64(m)) & (~active) & (~paid_off)
        active = active | newly

        live = active & (~paid_off)
        if not live.any():
            continue

        idx = np.where(live)[0]
        market_rate = float(mkt_m.loc[m, "market_rate_30yr"])
        short_rate = float(mkt_m.loc[m, "short_rate"])

        seasoning = (
            (np.datetime64(m) - fund_month[idx]).astype("timedelta64[D]").astype(float) / 30.44
        )
        seasoning = np.maximum(seasoning, 0)

        upb_bom = upb[idx].copy()
        status_bom = status[idx].copy()

        # ---- prepayment ----
        incentive = note_rate[idx] - market_rate
        cpr = prepay_cpr(incentive, seasoning, burnout[idx], is_investor[idx])
        smm = cpr_to_smm(cpr)

        # Delinquent loans do not prepay.
        can_prepay = np.isin(status_bom, ["Current", "30 DPD"])
        smm_eff = np.where(can_prepay, smm, 0.0)

        # ---- scheduled amortization ----
        mrate = note_rate[idx] / 100.0 / 12.0
        interest = upb_bom * mrate
        sched_prin = np.clip(pmt[idx] - interest, 0, upb_bom)
        # Nonperforming loans are not making payments.
        sched_prin = np.where(np.isin(status_bom, ["Current", "30 DPD"]), sched_prin, 0.0)

        after_sched = upb_bom - sched_prin

        # ---- unscheduled (prepay) ----
        full_payoff = rng.random(len(idx)) < smm_eff
        unsched = np.where(full_payoff, after_sched, 0.0)
        upb_eom = np.clip(after_sched - unsched, 0, None)

        # Burnout accumulates when a borrower had incentive but did not act.
        had_incentive = incentive > 0.5
        burnout[idx] = np.where(
            had_incentive & ~full_payoff,
            np.maximum(burnout[idx] * (1 - C.BURNOUT_DECAY), 0.30),
            burnout[idx],
        )

        # ---- credit migration ----
        risk_mult = _build_transition_matrix(
            fico[idx], ltv[idx], ltype[idx], occ[idx], seasoning
        )
        status_eom = migrate_delinquency(status_bom, risk_mult, rng)
        # A paid-off loan cannot be delinquent.
        status_eom = np.where(full_payoff, "Current", status_eom)

        # Liquidation (REO sold) removes the loan from the serviced portfolio,
        # same as a payoff -- the servicing asset is extinguished either way.
        liquidated = status_eom == "Liquidated"
        upb_eom = np.where(liquidated, 0.0, upb_eom)

        months_dq[idx] = np.where(status_eom == "Current", 0, months_dq[idx] + 1)

        # ---- escrow advances on nonperforming loans ----
        needs_adv = np.isin(status_eom, ["60 DPD", "90+ DPD", "Foreclosure", "REO"])
        monthly_adv = np.where(needs_adv & (escrow[idx] > 0), pmt[idx] * 0.31, 0.0)
        escrow_adv[idx] = np.where(needs_adv, escrow_adv[idx] + monthly_adv, 0.0)

        # ---- servicing cost ----
        years_elapsed = (m - pd.Timestamp(C.START_DATE)).days / 365.25
        drift = (1 + C.SERVICING_COST_ANNUAL_DRIFT) ** years_elapsed
        cost_mult = pd.Series(status_eom).map(C.SERVICING_COST_MULTIPLIER).fillna(1.0).values
        servicing_cost = C.SERVICING_COST_BASE * drift * cost_mult

        # ---- income ----
        float_income = (
            (upb_eom * 0.0022 + np.where(escrow[idx] > 0, upb_eom * 0.0041, 0.0))
            * (short_rate / 100.0)
            * C.FLOAT_SPREAD_TO_MARKET
        )
        ancillary = np.full(len(idx), C.ANCILLARY_INCOME_PER_LOAN_ANNUAL / 12.0)

        # ---- valuation ----
        mult_bom = msr_multiple(
            note_rate[idx], market_rate, cpr, status_bom, servicing_cost, short_rate, escrow[idx]
        )
        mult_eom = msr_multiple(
            note_rate[idx], market_rate, cpr, status_eom, servicing_cost, short_rate, escrow[idx]
        )

        # MSR value = UPB * multiple / 100 (multiple quoted in points).
        msr_bom = upb_bom * mult_bom / 100.0
        msr_eom = np.where(full_payoff | liquidated, 0.0, upb_eom * mult_eom / 100.0)

        # ---- roll-forward decomposition ----
        # Each component isolates one driver, holding the others fixed, so the
        # pieces sum to the total change (residual absorbs interaction terms).
        runoff_impact = -(sched_prin * mult_bom / 100.0)
        prepay_impact = -(unsched * mult_bom / 100.0)
        value_after_balance = msr_bom + runoff_impact + prepay_impact
        credit_impact = np.where(
            full_payoff | liquidated, 0.0, upb_eom * (mult_eom - mult_bom) / 100.0
        )
        residual = msr_eom - (value_after_balance + credit_impact)

        is_new = (fund_month[idx] == np.datetime64(m))

        rows.append(
            pd.DataFrame(
                {
                    "loan_id": loan_id[idx],
                    "as_of_month": m,
                    "seasoning_months": np.round(seasoning, 1),
                    "upb_beginning": np.round(upb_bom, 2),
                    "upb_ending": np.round(upb_eom, 2),
                    "scheduled_principal": np.round(sched_prin, 2),
                    "unscheduled_principal": np.round(unsched, 2),
                    "note_rate": note_rate[idx],
                    "market_rate_30yr": round(market_rate, 3),
                    "short_rate": round(short_rate, 3),
                    "rate_incentive": np.round(incentive, 3),
                    "annualized_cpr": np.round(cpr, 4),
                    "smm": np.round(smm, 5),
                    "delinquency_status": status_eom,
                    "months_delinquent": months_dq[idx],
                    "servicing_cost_annual": np.round(servicing_cost, 2),
                    "escrow_advance_balance": np.round(escrow_adv[idx], 2),
                    "float_income": np.round(float_income, 2),
                    "ancillary_income": np.round(ancillary, 2),
                    "msr_multiple": np.round(mult_eom, 4),
                    "msr_value_bom": np.round(msr_bom, 2),
                    "msr_value_eom": np.round(msr_eom, 2),
                    "rf_runoff": np.round(runoff_impact, 2),
                    "rf_prepay": np.round(prepay_impact, 2),
                    "rf_credit_and_rate": np.round(credit_impact, 2),
                    "rf_residual": np.round(residual, 2),
                    "is_new_addition": is_new.astype(int),
                    "paid_off_this_month": full_payoff.astype(int),
                    "liquidated_this_month": liquidated.astype(int),
                }
            )
        )

        # ---- commit state ----
        upb[idx] = upb_eom
        status[idx] = status_eom
        # Both payoff and liquidation terminate the servicing relationship.
        exited = full_payoff | liquidated
        newly_gone = idx[exited]
        paid_off[newly_gone] = True
        payoff_month[newly_gone] = np.datetime64(m)

    panel = pd.concat(rows, ignore_index=True)
    return panel


def build_rate_shock_scenarios(loans: pd.DataFrame, panel: pd.DataFrame) -> pd.DataFrame:
    """Precomputed portfolio MSR value under parallel rate shocks.

    Powers the interactive what-if parameter. Values are computed at the final
    reporting month so the control re-prices the portfolio as it stands today.
    """
    last_month = panel.as_of_month.max()
    snap = panel[panel.as_of_month == last_month].copy()
    snap = snap[snap.upb_ending > 0]

    meta = loans.set_index("loan_id")[["escrow_flag", "occupancy", "loan_type", "fico"]]
    snap = snap.join(meta, on="loan_id")
    snap["escrow_flag"] = pd.to_numeric(snap.escrow_flag, errors="coerce").fillna(0).astype(float)

    base_market = float(snap.market_rate_30yr.iloc[0])
    base_short = float(snap.short_rate.iloc[0])

    out = []
    for shock_bp in C.RATE_SHOCK_BUCKETS:
        shift = shock_bp / 100.0
        new_market = base_market + shift
        new_short = max(base_short + shift, 0.05)

        incentive = snap.note_rate.values - new_market
        cpr = prepay_cpr(
            incentive,
            snap.seasoning_months.values,
            np.ones(len(snap)),
            (snap.occupancy == "Investment").values,
        )
        mult = msr_multiple(
            snap.note_rate.values,
            new_market,
            cpr,
            snap.delinquency_status.values,
            snap.servicing_cost_annual.values,
            new_short,
            snap.escrow_flag.values,
        )
        value = snap.upb_ending.values * mult / 100.0

        out.append(
            pd.DataFrame(
                {
                    "loan_id": snap.loan_id.values,
                    "as_of_month": last_month,
                    "rate_shock_bp": shock_bp,
                    "shocked_market_rate": round(new_market, 3),
                    "scenario_cpr": np.round(cpr, 4),
                    "scenario_multiple": np.round(mult, 4),
                    "scenario_msr_value": np.round(value, 2),
                }
            )
        )

    return pd.concat(out, ignore_index=True)
