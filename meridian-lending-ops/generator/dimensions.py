"""Dimension tables and the rate curve that drives the whole simulation."""

import numpy as np
import pandas as pd

import config as C


def build_dim_date() -> pd.DataFrame:
    """Calendar spanning the origination window plus a year of runway for
    post-closing and servicing activity that spills past it."""
    days = pd.date_range(C.START_DATE, "2026-12-31", freq="D")
    holidays = set(pd.to_datetime(C.US_HOLIDAYS))

    df = pd.DataFrame({"date_key": days})
    df["year"] = df.date_key.dt.year
    df["quarter"] = "Q" + df.date_key.dt.quarter.astype(str)
    df["month_num"] = df.date_key.dt.month
    df["month_name"] = df.date_key.dt.strftime("%b")
    df["month_start"] = df.date_key.values.astype("datetime64[M]")
    df["week_start"] = df.date_key - pd.to_timedelta(df.date_key.dt.weekday, unit="D")
    df["day_of_week"] = df.date_key.dt.day_name()
    df["is_weekend"] = df.date_key.dt.weekday.isin([5, 6]).astype(int)
    df["is_holiday"] = df.date_key.isin(holidays).astype(int)
    df["is_business_day"] = ((df.is_weekend == 0) & (df.is_holiday == 0)).astype(int)
    df["is_month_end"] = (df.date_key.dt.is_month_end).astype(int)

    # Month-end funding rush: the last five business days of a month carry
    # disproportionate funding volume.
    df["days_to_month_end"] = (df.date_key + pd.offsets.MonthEnd(0) - df.date_key).dt.days
    df["is_month_end_rush"] = (df.days_to_month_end <= 4).astype(int)

    df["fiscal_period"] = df.date_key.dt.strftime("%Y-%m")
    return df


def build_dim_rates() -> pd.DataFrame:
    """Weekly 30-year fixed rate.

    Shape: a slow drift down through H1, a sharp ~110bps drop over eight weeks
    starting at SHOCK_START, a trough, then a partial retrace through year two.
    Everything downstream keys off this series.
    """
    r = C.rng(1)
    weeks = pd.date_range(C.START_DATE, "2026-12-31", freq="W-MON")
    shock_start = pd.Timestamp(C.SHOCK_START)

    rates = []
    for w in weeks:
        if w < shock_start:
            # Gentle drift down into the shock.
            progress = (w - pd.Timestamp(C.START_DATE)).days / max(
                (shock_start - pd.Timestamp(C.START_DATE)).days, 1
            )
            base = C.RATE_START - 0.22 * progress
        elif w < shock_start + pd.Timedelta(weeks=C.SHOCK_WEEKS):
            # The drop itself -- eased so it accelerates then decelerates.
            wk = (w - shock_start).days / 7.0
            t = wk / C.SHOCK_WEEKS
            eased = t * t * (3 - 2 * t)          # smoothstep
            base = (C.RATE_START - 0.22) - eased * (C.RATE_START - 0.22 - C.RATE_TROUGH)
        else:
            # Trough, then partial retrace.
            since = (w - (shock_start + pd.Timedelta(weeks=C.SHOCK_WEEKS))).days / 7.0
            retrace = 1 - np.exp(-since / 34.0)
            base = C.RATE_TROUGH + retrace * (C.RATE_END - C.RATE_TROUGH)
        rates.append(base)

    rates = np.array(rates)
    # Weekly noise, smoothed -- rate series are autocorrelated, not white.
    noise = r.normal(0, 0.055, len(rates))
    noise = pd.Series(noise).rolling(3, min_periods=1).mean().values
    rates = rates + noise

    df = pd.DataFrame({"week_start": weeks, "market_rate_30yr": np.round(rates, 3)})
    df["market_rate_15yr"] = np.round(df.market_rate_30yr - 0.62 + r.normal(0, 0.03, len(df)), 3)
    # Short rate drives float income on escrow balances.
    df["short_rate"] = np.round(
        np.clip(df.market_rate_30yr - 2.35 + r.normal(0, 0.04, len(df)), 0.15, None), 3
    )
    df["rate_4wk_change"] = np.round(df.market_rate_30yr.diff(4).fillna(0), 3)
    return df


def daily_rate_lookup(dim_rates: pd.DataFrame) -> pd.Series:
    """Forward-filled daily rate series indexed by date, for per-loan lookups."""
    s = dim_rates.set_index("week_start")["market_rate_30yr"]
    daily = s.reindex(pd.date_range(C.START_DATE, "2026-12-31", freq="D")).ffill().bfill()
    return daily


def daily_short_rate_lookup(dim_rates: pd.DataFrame) -> pd.Series:
    s = dim_rates.set_index("week_start")["short_rate"]
    daily = s.reindex(pd.date_range(C.START_DATE, "2026-12-31", freq="D")).ffill().bfill()
    return daily


def build_dim_geography() -> pd.DataFrame:
    rows = []
    for i, (state, county, msa, rural, panel) in enumerate(C.GEOGRAPHY, start=1):
        rows.append(
            {
                "geo_id": f"GEO-{i:03d}",
                "state": state,
                "county": county,
                "msa": msa,
                "rural_flag": rural,
                "appraiser_panel_depth": panel,
                "market_tier": "Rural" if rural else "Metro",
            }
        )
    df = pd.DataFrame(rows)
    # Panel depth relative to the median gives a clean capacity signal that the
    # appraisal wait model consumes directly.
    df["panel_capacity_index"] = np.round(
        df.appraiser_panel_depth / df.appraiser_panel_depth.median(), 3
    )
    return df


def build_dim_branch(dim_geo: pd.DataFrame) -> pd.DataFrame:
    r = C.rng(2)
    rows = []
    for bid, name, state, region in C.BRANCHES:
        # Branch size scales loosely with the state's metro presence.
        state_geo = dim_geo[dim_geo.state == state]
        metro_share = 1 - state_geo.rural_flag.mean() if len(state_geo) else 0.5
        size = r.choice(
            ["Large", "Medium", "Small"],
            p=[0.45, 0.35, 0.20] if metro_share > 0.6 else [0.15, 0.40, 0.45],
        )
        rows.append(
            {
                "branch_id": bid,
                "branch_name": name,
                "state": state,
                "region": region,
                "branch_size": size,
                "opened_date": pd.Timestamp("2015-01-01")
                + pd.Timedelta(days=int(r.integers(0, 2900))),
            }
        )
    return pd.DataFrame(rows)


def build_dim_branch_staffing(dim_branch: pd.DataFrame) -> pd.DataFrame:
    """Monthly headcount by branch and role.

    Staffing responds to volume, but with a lag -- which is exactly why capacity
    lags demand during the surge and the queues build.
    """
    r = C.rng(3)
    months = pd.date_range(C.START_DATE, C.END_DATE, freq="MS")
    size_base = {"Large": 14, "Medium": 9, "Small": 5}
    shock = pd.Timestamp(C.SHOCK_START)

    rows = []
    for _, b in dim_branch.iterrows():
        base = size_base[b.branch_size]
        for m in months:
            # Hiring response lags the shock by ~3 months and is partial.
            months_since = (m - shock).days / 30.44
            if months_since <= 2:
                lift = 1.0
            else:
                lift = 1.0 + min(0.28, 0.055 * (months_since - 2))
            lo = max(2, int(round(base * lift + r.normal(0, 0.8))))
            rows.append(
                {
                    "branch_id": b.branch_id,
                    "month_start": m,
                    "loan_officers": lo,
                    "processors": max(1, int(round(lo * 0.42 + r.normal(0, 0.5)))),
                    "underwriters": max(1, int(round(lo * 0.31 + r.normal(0, 0.4)))),
                    "closers": max(1, int(round(lo * 0.24 + r.normal(0, 0.4)))),
                }
            )
    return pd.DataFrame(rows)


def build_dim_employee(dim_branch: pd.DataFrame) -> pd.DataFrame:
    """Loan officers, processors, underwriters, closers.

    Experience tier drives capacity and (for underwriters) condition-issuance
    behavior -- junior underwriters issue more conditions, which feeds the
    rework story.
    """
    r = C.rng(4)
    rows = []
    counters = {"LO": 0, "PR": 0, "UW": 0, "CL": 0}
    size_counts = {
        "Large": {"LO": 14, "PR": 6, "UW": 5, "CL": 4},
        "Medium": {"LO": 9, "PR": 4, "UW": 3, "CL": 2},
        "Small": {"LO": 5, "PR": 2, "UW": 2, "CL": 2},
    }

    for _, b in dim_branch.iterrows():
        for role_code, n in size_counts[b.branch_size].items():
            role_name = {
                "LO": "Loan Officer",
                "PR": "Processor",
                "UW": "Underwriter",
                "CL": "Closer",
            }[role_code]
            for _ in range(n):
                counters[role_code] += 1
                tier = r.choice(["Senior", "Mid", "Junior"], p=[0.28, 0.44, 0.28])
                tenure_years = {
                    "Senior": r.uniform(6, 18),
                    "Mid": r.uniform(2.5, 6),
                    "Junior": r.uniform(0.2, 2.5),
                }[tier]
                cap = {"Senior": 1.24, "Mid": 1.0, "Junior": 0.72}[tier]
                rows.append(
                    {
                        "employee_id": f"{role_code}-{counters[role_code]:04d}",
                        "role": role_name,
                        "branch_id": b.branch_id,
                        "region": b.region,
                        "experience_tier": tier,
                        "tenure_years": round(float(tenure_years), 1),
                        "hire_date": pd.Timestamp(C.END_DATE)
                        - pd.Timedelta(days=int(tenure_years * 365.25)),
                        "capacity_index": cap,
                    }
                )
    return pd.DataFrame(rows)


def build_dim_vendor() -> pd.DataFrame:
    rows = []
    for vid, name, states, sla in C.VENDORS:
        rows.append(
            {
                "vendor_id": vid,
                "vendor_name": name,
                "vendor_type": "Appraisal Management Company",
                "coverage_states": "ALL" if states is None else ",".join(states),
                "sla_days": sla,
                "is_national": int(states is None),
            }
        )
    return pd.DataFrame(rows)


def build_all():
    dim_date = build_dim_date()
    dim_rates = build_dim_rates()
    dim_geo = build_dim_geography()
    dim_branch = build_dim_branch(dim_geo)
    dim_staffing = build_dim_branch_staffing(dim_branch)
    dim_employee = build_dim_employee(dim_branch)
    dim_vendor = build_dim_vendor()
    return {
        "dim_date": dim_date,
        "dim_rates": dim_rates,
        "dim_geography": dim_geo,
        "dim_branch": dim_branch,
        "dim_branch_staffing": dim_staffing,
        "dim_employee": dim_employee,
        "dim_vendor": dim_vendor,
    }
