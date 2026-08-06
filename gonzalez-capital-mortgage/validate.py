"""Validation harness: asserts the generated data actually tells the story.

Run after generation. Every check maps to a claim the dashboard makes, so if a
parameter is retuned and a claim stops holding, this fails loudly rather than
letting a broken narrative ship.

    python validate.py
"""

import os
import sqlite3
import sys

import pandas as pd

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gonzalez_capital_mortgage.db")

results = []


def check(label, passed, detail=""):
    results.append((label, passed, detail))
    mark = "PASS" if passed else "FAIL"
    print(f"  [{mark}] {label}")
    if detail:
        print(f"         {detail}")


def q(con, sql):
    return pd.read_sql(sql, con)


def main():
    if not os.path.exists(DB):
        print("gonzalez_capital_mortgage.db not found -- run: python generator/build.py")
        return 1

    con = sqlite3.connect(DB)

    print("\n" + "=" * 70)
    print("GONZALEZ CAPITAL MORTGAGE DATA VALIDATION")
    print("=" * 70)

    # ---------------------------------------------------- structural checks
    print("\n[Structure]")
    tables = q(con, "SELECT name FROM sqlite_master WHERE type='table'").name.tolist()
    views = q(con, "SELECT name FROM sqlite_master WHERE type='view'").name.tolist()
    expected_tables = [
        "fact_loan", "fact_condition", "fact_daily_queue", "fact_msr_monthly",
        "fact_msr_rate_shock", "dim_branch", "dim_branch_staffing",
        "dim_employee", "dim_geography", "dim_vendor", "dim_rates", "dim_date",
    ]
    check("all 12 tables present",
          all(t in tables for t in expected_tables),
          f"found {len(tables)}")
    check("7 analytical views present", len(views) == 7, f"found {len(views)}")

    n_loans = q(con, "SELECT COUNT(*) c FROM fact_loan").c[0]
    check("loan count in 20k-25k range", 20000 <= n_loans <= 25000, f"{n_loans:,} loans")

    # referential integrity
    orphan_cond = q(con, """SELECT COUNT(*) c FROM fact_condition c
                            LEFT JOIN fact_loan l ON l.loan_id=c.loan_id
                            WHERE l.loan_id IS NULL""").c[0]
    check("no orphaned conditions", orphan_cond == 0, f"{orphan_cond} orphans")

    orphan_msr = q(con, """SELECT COUNT(*) c FROM fact_msr_monthly m
                           LEFT JOIN fact_loan l ON l.loan_id=m.loan_id
                           WHERE l.loan_id IS NULL""").c[0]
    check("no orphaned MSR rows", orphan_msr == 0, f"{orphan_msr} orphans")

    # ------------------------------------------------- the bottleneck story
    print("\n[Story: the bottleneck moved]")
    s = q(con, """SELECT shock_period,
                         AVG(days_appraisal_wait)     appr,
                         AVG(days_uw_touch)           uw_touch,
                         AVG(days_uw_queue_wait)      uw_wait,
                         AVG(days_condition_clearing) cond,
                         AVG(condition_rounds)        rounds,
                         AVG(days_total_cycle)        cycle
                  FROM fact_loan
                  WHERE status='Funded' AND days_total_cycle > 0
                    AND days_uw_queue_wait >= 0
                  GROUP BY shock_period""").set_index("shock_period")

    pre, post = s.loc["Pre-Shock"], s.loc["Post-Shock"]

    check("cycle time materially worse post-shock",
          post.cycle > pre.cycle * 1.30,
          f"{pre.cycle:.1f}d -> {post.cycle:.1f}d (+{(post.cycle/pre.cycle-1)*100:.0f}%)")

    check("appraisal wait more than doubled",
          post.appr > pre.appr * 2.0,
          f"{pre.appr:.1f}d -> {post.appr:.1f}d (+{(post.appr/pre.appr-1)*100:.0f}%)")

    check("underwriting QUEUE stayed flat (not the bottleneck)",
          post.uw_wait < pre.uw_wait * 1.6,
          f"{pre.uw_wait:.1f}d -> {post.uw_wait:.1f}d")

    check("appraisal grew far faster than UW touch time",
          (post.appr / pre.appr) > (post.uw_touch / pre.uw_touch) * 1.8,
          f"appraisal +{(post.appr/pre.appr-1)*100:.0f}% vs "
          f"UW touch +{(post.uw_touch/pre.uw_touch-1)*100:.0f}%")

    check("condition rework increased",
          post.rounds > pre.rounds * 1.20,
          f"{pre.rounds:.2f} -> {post.rounds:.2f} rounds/loan")

    # -------------------------------------------------- geographic finding
    print("\n[Story: geographic concentration]")
    g = q(con, """SELECT market_tier,
                         COUNT(*)                 loans,
                         SUM(days_appraisal_wait) total_days,
                         AVG(days_appraisal_wait) avg_wait
                  FROM fact_loan
                  WHERE days_appraisal_wait IS NOT NULL
                    AND shock_period='Post-Shock'
                  GROUP BY market_tier""").set_index("market_tier")

    rural_vol_share = g.loc["Rural", "loans"] / g.loans.sum()
    rural_delay_share = g.loc["Rural", "total_days"] / g.total_days.sum()

    check("rural is a minority of volume",
          0.15 <= rural_vol_share <= 0.30,
          f"{rural_vol_share*100:.0f}% of loans")
    check("rural carries disproportionate delay",
          rural_delay_share > rural_vol_share * 1.6,
          f"{rural_delay_share*100:.0f}% of appraisal delay days")
    check("rural appraisal wait materially worse than metro",
          g.loc["Rural", "avg_wait"] > g.loc["Metro", "avg_wait"] * 1.8,
          f"rural {g.loc['Rural','avg_wait']:.1f}d vs "
          f"metro {g.loc['Metro','avg_wait']:.1f}d")

    # ------------------------------------------------------------- fallout
    print("\n[Story: fallout cost]")
    fo = q(con, """SELECT shock_period,
                          100.0*SUM(CASE WHEN status='Fallout' THEN 1 ELSE 0 END)/COUNT(*) pct,
                          SUM(CASE WHEN status='Fallout' THEN loan_amount ELSE 0 END)/1e6 lost_m
                   FROM fact_loan GROUP BY shock_period""").set_index("shock_period")
    check("fallout rate rose after the shock",
          fo.loc["Post-Shock", "pct"] > fo.loc["Pre-Shock", "pct"] * 1.5,
          f"{fo.loc['Pre-Shock','pct']:.1f}% -> {fo.loc['Post-Shock','pct']:.1f}%")
    check("fallout dollars are material",
          fo.loc["Post-Shock", "lost_m"] > 500,
          f"${fo.loc['Post-Shock','lost_m']:,.0f}M lost volume post-shock")

    # ----------------------------------------------------------- MSR model
    print("\n[MSR behavior]")
    shock = q(con, """SELECT rate_shock_bp, SUM(scenario_msr_value)/1e6 msr,
                             AVG(scenario_cpr) cpr
                      FROM fact_msr_rate_shock
                      GROUP BY rate_shock_bp ORDER BY rate_shock_bp""").set_index("rate_shock_bp")

    check("MSR value rises monotonically with rates",
          shock.msr.is_monotonic_increasing,
          f"-200bp ${shock.msr.iloc[0]:.0f}M -> +200bp ${shock.msr.iloc[-1]:.0f}M")
    check("CPR falls monotonically as rates rise",
          shock.cpr.is_monotonic_decreasing,
          f"{shock.cpr.iloc[0]*100:.0f}% -> {shock.cpr.iloc[-1]*100:.0f}%")

    base = shock.loc[0, "msr"]
    down = (shock.loc[-100, "msr"] / base - 1) * 100
    up = (shock.loc[100, "msr"] / base - 1) * 100
    check("downside sensitivity is realistic (-15% to -45% at -100bp)",
          -45 <= down <= -15, f"{down:+.1f}% at -100bp")
    check("upside is muted vs downside (asymmetry)",
          abs(up) < abs(down), f"{up:+.1f}% at +100bp vs {down:+.1f}% at -100bp")

    # prepay responds to the historical rate drop
    hist = q(con, """SELECT as_of_month, AVG(annualized_cpr) cpr, AVG(msr_multiple) mult,
                            AVG(market_rate_30yr) rate
                     FROM fact_msr_monthly WHERE upb_ending > 0
                     GROUP BY as_of_month ORDER BY as_of_month""").set_index("as_of_month")
    pre_r = hist.loc["2024-08-01"]
    post_r = hist.loc["2024-11-01"]
    check("CPR spiked when rates fell (historical)",
          post_r.cpr > pre_r.cpr * 1.35,
          f"{pre_r.cpr*100:.1f}% -> {post_r.cpr*100:.1f}% as rate "
          f"{pre_r.rate:.2f} -> {post_r.rate:.2f}")
    check("multiple compressed when rates fell",
          post_r.mult < pre_r.mult * 0.95,
          f"{pre_r.mult:.2f}x -> {post_r.mult:.2f}x")

    # credit realism
    dq = q(con, """SELECT delinquency_status, COUNT(*) n FROM fact_msr_monthly
                   WHERE upb_ending > 0 AND as_of_month = (SELECT MAX(as_of_month)
                   FROM fact_msr_monthly) GROUP BY delinquency_status""")
    total = dq.n.sum()
    current_pct = dq[dq.delinquency_status == "Current"].n.sum() / total * 100
    serious = dq[dq.delinquency_status.isin(["90+ DPD", "Foreclosure", "REO"])].n.sum() / total * 100
    check("portfolio is mostly performing (92-99% current)",
          92 <= current_pct <= 99, f"{current_pct:.1f}% current")
    check("serious delinquency is realistic (<4%)",
          serious < 4.0, f"{serious:.2f}% in 90+/FC/REO")

    # ------------------------------------------- product eligibility rules
    # These catch loans that could not exist in reality -- the first thing a
    # mortgage professional would spot in a dashboard screenshot.
    print("\n[Product eligibility]")

    bad_jumbo_ltv = q(con, """SELECT COUNT(*) c FROM fact_loan
                              WHERE loan_type='Jumbo' AND ltv > 90""").c[0]
    check("no jumbo above 90% LTV", bad_jumbo_ltv == 0, f"{bad_jumbo_ltv} violations")

    small_jumbo = q(con, f"""SELECT COUNT(*) c FROM fact_loan
                             WHERE loan_type='Jumbo' AND loan_amount <= 806500""").c[0]
    check("all jumbo loans exceed the conforming limit",
          small_jumbo == 0, f"{small_jumbo} below $806,500")

    big_conv = q(con, """SELECT COUNT(*) c FROM fact_loan
                         WHERE loan_type='Conventional' AND loan_amount > 806500""").c[0]
    check("no conventional above the conforming limit",
          big_conv == 0, f"{big_conv} violations")

    big_fha = q(con, """SELECT COUNT(*) c FROM fact_loan
                        WHERE loan_type='FHA' AND loan_amount > 524225""").c[0]
    check("no FHA above the FHA ceiling", big_fha == 0, f"{big_fha} violations")

    big_usda = q(con, """SELECT COUNT(*) c FROM fact_loan
                         WHERE loan_type='USDA' AND loan_amount > 377600""").c[0]
    check("no USDA above its practical limit", big_usda == 0, f"{big_usda} violations")

    gov_investment = q(con, """SELECT COUNT(*) c FROM fact_loan
                               WHERE loan_type IN ('FHA','VA','USDA')
                               AND TRIM(UPPER(occupancy)) <> 'PRIMARY RESIDENCE'""").c[0]
    check("no government loans on non-owner-occupied property",
          gov_investment == 0, f"{gov_investment} violations")

    inv_ltv = q(con, """SELECT COUNT(*) c FROM fact_loan
                        WHERE TRIM(UPPER(occupancy))='INVESTMENT' AND ltv > 85""").c[0]
    check("no investment property above 85% LTV", inv_ltv == 0, f"{inv_ltv} violations")

    usda_cashout = q(con, """SELECT COUNT(*) c FROM fact_loan
                             WHERE loan_type='USDA' AND loan_purpose='Cash-Out Refi'""").c[0]
    check("no USDA cash-out refinances", usda_cashout == 0, f"{usda_cashout} violations")

    cashout_ltv = q(con, """SELECT COUNT(*) c FROM fact_loan
                            WHERE loan_purpose='Cash-Out Refi'
                            AND loan_type='Conventional' AND ltv > 80""").c[0]
    check("conventional cash-out capped at 80% LTV",
          cashout_ltv == 0, f"{cashout_ltv} violations")

    low_fico = q(con, """SELECT COUNT(*) c FROM fact_loan
                         WHERE (loan_type='Conventional' AND fico < 620)
                            OR (loan_type='Jumbo' AND fico < 700)
                            OR (loan_type='USDA' AND fico < 640)""").c[0]
    check("FICO respects product minimums", low_fico == 0, f"{low_fico} violations")

    cltv_bad = q(con, "SELECT COUNT(*) c FROM fact_loan WHERE cltv < ltv").c[0]
    check("CLTV never below LTV", cltv_bad == 0, f"{cltv_bad} violations")

    gov_pricing = q(con, """SELECT
        AVG(CASE WHEN loan_type='VA' THEN note_rate END) va,
        AVG(CASE WHEN loan_type='Conventional' THEN note_rate END) conv
        FROM fact_loan WHERE fico BETWEEN 700 AND 760""")
    check("VA prices below conventional at equal credit",
          gov_pricing.va[0] < gov_pricing.conv[0],
          f"VA {gov_pricing.va[0]:.2f}% vs Conventional {gov_pricing.conv[0]:.2f}%")

    # ------------------------------------------------------ data artifacts
    print("\n[Deliberate data-quality artifacts]")
    nulls = q(con, """SELECT channel,
                             100.0*SUM(CASE WHEN dti IS NULL THEN 1 ELSE 0 END)/COUNT(*) pct
                      FROM fact_loan GROUP BY channel""").set_index("channel")
    check("missingness is non-random (Wholesale worst)",
          nulls.loc["Wholesale", "pct"] > nulls.loc["Retail", "pct"] * 2,
          f"Wholesale {nulls.loc['Wholesale','pct']:.1f}% vs "
          f"Retail {nulls.loc['Retail','pct']:.1f}%")

    quirk = q(con, """SELECT COUNT(*) c FROM fact_loan
                      WHERE branch_id='BR-502' AND dti < 1 AND dti IS NOT NULL""").c[0]
    check("DTI unit quirk present at BR-502", quirk > 100, f"{quirk} decimal-format rows")

    dupes = q(con, "SELECT COUNT(*) c FROM fact_loan WHERE is_reapplication=1").c[0]
    check("duplicate reapplications present", dupes >= 30, f"{dupes} rows")

    anom = q(con, "SELECT COUNT(*) c FROM fact_loan WHERE days_uw_queue_wait < 0").c[0]
    check("out-of-sequence timestamps produce negative durations",
          anom >= 40, f"{anom} rows with negative UW queue wait")

    variants = q(con, """SELECT COUNT(DISTINCT property_type) c FROM fact_loan""").c[0]
    check("case/whitespace variants present", variants > 8,
          f"{variants} distinct property_type strings (5 real values)")

    outage = q(con, """SELECT substr(appraisal_ordered,1,7) mo, AVG(days_appraisal_wait) w
                       FROM fact_loan WHERE amc_vendor_id='AMC-02'
                       AND substr(appraisal_ordered,1,7) IN ('2025-01','2025-02','2025-03')
                       GROUP BY mo""").set_index("mo")
    check("AMC-02 outage visible as a February spike",
          outage.loc["2025-02", "w"] > outage.loc["2025-01", "w"] * 1.25,
          f"Jan {outage.loc['2025-01','w']:.1f}d -> Feb {outage.loc['2025-02','w']:.1f}d "
          f"-> Mar {outage.loc['2025-03','w']:.1f}d")

    con.close()

    # ------------------------------------------------------------ summary
    passed = sum(1 for _, p, _ in results if p)
    total_checks = len(results)
    print("\n" + "=" * 70)
    print(f"{passed}/{total_checks} checks passed")
    print("=" * 70 + "\n")

    if passed < total_checks:
        print("Failed checks:")
        for label, p, detail in results:
            if not p:
                print(f"  - {label}: {detail}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
