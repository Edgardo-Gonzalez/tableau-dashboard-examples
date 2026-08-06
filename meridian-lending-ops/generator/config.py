"""Simulation constants for Meridian Home Lending.

All data produced by this package is synthetic. Values are chosen to be
plausible for a mid-size US mortgage lender, not to match any real institution.
"""

import numpy as np

SEED = 20260806

# ---------------------------------------------------------------- time window
START_DATE = "2024-01-01"
END_DATE = "2025-12-31"

# The rate shock: rates fall ~110bps starting here, driving the volume surge
# that the whole narrative hangs on.
SHOCK_START = "2024-08-05"
SHOCK_WEEKS = 8

# Pre/post comparison windows used by the dashboard's before/after framing.
PRE_SHOCK_END = "2024-07-31"
POST_SHOCK_START = "2024-10-01"

TARGET_APPLICATIONS = 22_000

# ---------------------------------------------------------------- rate curve
RATE_START = 7.05          # 30-yr fixed at the start of the window
RATE_TROUGH = 5.85         # after the shock
RATE_END = 6.60            # partial retrace through year 2

# ------------------------------------------------------------------ channels
CHANNELS = ["Retail", "Wholesale", "Correspondent"]
CHANNEL_MIX = [0.52, 0.33, 0.15]

LOAN_PURPOSES = ["Purchase", "Rate-Term Refi", "Cash-Out Refi"]
# Purchase-heavy before the shock; refi share explodes after it. The generator
# interpolates between these two mixes based on rate incentive.
PURPOSE_MIX_HIGH_RATE = [0.78, 0.08, 0.14]
PURPOSE_MIX_LOW_RATE = [0.41, 0.42, 0.17]

LOAN_TYPES = ["Conventional", "FHA", "VA", "USDA", "Jumbo"]
LOAN_TYPE_MIX = [0.58, 0.16, 0.13, 0.04, 0.09]

# ------------------------------------------------- product eligibility rules
# Real products have hard limits. Without these the data contains loans that
# could not exist -- a $1.9M FHA, a 96.5% LTV jumbo -- which is exactly what a
# mortgage professional spots first in a dashboard screenshot.
CONFORMING_LIMIT = 806_500      # 2025 baseline one-unit conforming limit
FHA_LIMIT_STANDARD = 524_225    # typical one-unit FHA ceiling
USDA_PRACTICAL_LIMIT = 377_600  # USDA is income/area limited; no formal cap
VA_PRACTICAL_LIMIT = 1_500_000

# Max LTV by product, for purchase transactions.
MAX_LTV_PURCHASE = {
    "Conventional": 97.0,
    "FHA": 96.5,
    "VA": 100.0,
    "USDA": 100.0,
    "Jumbo": 89.9,      # jumbo underwriting is materially tighter
}

# Cash-out refinance is capped well below rate-term across every product.
MAX_LTV_CASHOUT = {
    "Conventional": 80.0,
    "FHA": 80.0,
    "VA": 90.0,
    "USDA": 0.0,        # USDA does not permit cash-out
    "Jumbo": 75.0,
}

MAX_LTV_RATE_TERM = {
    "Conventional": 95.0,
    "FHA": 97.75,
    "VA": 100.0,
    "USDA": 100.0,
    "Jumbo": 80.0,
}

# Occupancy overlays -- these bind on top of the product limit.
MAX_LTV_INVESTMENT = 85.0
MAX_LTV_SECOND_HOME = 90.0

# Minimum FICO by product.
MIN_FICO = {
    "Conventional": 620,
    "FHA": 580,
    "VA": 580,
    "USDA": 640,
    "Jumbo": 700,
}

# Products unavailable for certain occupancies.
# FHA/VA/USDA are owner-occupancy programs.
GOVERNMENT_PRODUCTS = ["FHA", "VA", "USDA"]

OCCUPANCY = ["Primary Residence", "Second Home", "Investment"]
OCCUPANCY_MIX = [0.84, 0.06, 0.10]

PROPERTY_TYPES = ["Single Family", "Condo", "Townhome", "2-4 Unit", "Manufactured"]
PROPERTY_TYPE_MIX = [0.68, 0.13, 0.12, 0.05, 0.02]

EMPLOYMENT_TYPES = ["W2", "Self-Employed", "Retired", "Mixed"]
EMPLOYMENT_MIX = [0.68, 0.19, 0.07, 0.06]

# ------------------------------------------------------------------ capacity
# Underwriting capacity is partially elastic (overtime, contractors). Appraisal
# capacity is external and effectively fixed -- which is why the bottleneck
# migrates there under load. This asymmetry is the engine of the whole story.
#
# Capacity is expressed as a TARGET UTILIZATION against pre-shock demand rather
# than an absolute daily count, so it stays calibrated if volume is retuned.
# Pre-shock utilization sits below the congestion knee (queues are stable);
# the surge then pushes utilization well past it.
PRESHOCK_UW_UTILIZATION = 0.63          # comfortable, below the 0.80 knee
PRESHOCK_PROCESSING_UTILIZATION = 0.55  # processing has more slack
PRESHOCK_APPRAISAL_UTILIZATION = 0.68   # appraisal starts tightest

UW_SURGE_ELASTICITY = 0.55      # fraction of surge absorbable via overtime
PROCESSING_ELASTICITY = 0.45
# Appraisal has NO elasticity term -- it is an external vendor constraint.

# Congestion curve: flat until utilization ~0.80, then bends hard.
CONGESTION_KNEE = 0.80
CONGESTION_EXPONENT = 1.75
CONGESTION_CEILING = 2.85       # max multiplier on baseline stage duration

# --------------------------------------------------------------- stage bases
# Baseline (uncongested) durations in calendar days.
BASE_DOC_COLLECTION = 3.5
BASE_PROCESSING_TOUCH = 4.0
BASE_APPRAISAL_WAIT_METRO = 8.0
BASE_APPRAISAL_WAIT_RURAL = 9.0
BASE_UW_TOUCH = 4.2
BASE_CONDITION_ROUND = 4.0      # borrower response time per round
BASE_CTC_TO_FUNDING = 4.5

# Appraisal panels in rural markets are thin, so the AMC deprioritizes those
# orders during surge. Applied on top of congestion, scaled by how far past
# the knee utilization has gone.
RURAL_SURGE_PENALTY = 1.78

# ------------------------------------------------------------------ fallout
# Fallout probability rises with elapsed cycle time -- a borrower quoted 30 days
# who is still waiting on day 50 goes shopping elsewhere.
FALLOUT_BASE = 0.058
FALLOUT_DAYS_SENSITIVITY = 0.0062
FALLOUT_CAP = 0.34

FALLOUT_REASONS = [
    "Borrower Withdrew - Rate Shopping",
    "Borrower Withdrew - Timeline",
    "Denied - DTI",
    "Denied - Credit",
    "Denied - Collateral/Appraisal",
    "Denied - Insufficient Assets",
    "Borrower Withdrew - Property Issue",
    "Denied - Employment Verification",
]

# ---------------------------------------------------------------- geography
# (state, county, msa, rural_flag, panel_depth)  panel_depth = # of active
# appraisers on the panel covering that county.
GEOGRAPHY = [
    # --- metro-dense states ---
    ("TX", "Harris County", "Houston-The Woodlands-Sugar Land", 0, 42),
    ("TX", "Dallas County", "Dallas-Fort Worth-Arlington", 0, 46),
    ("TX", "Travis County", "Austin-Round Rock-Georgetown", 0, 31),
    ("TX", "Bexar County", "San Antonio-New Braunfels", 0, 28),
    ("TX", "Collin County", "Dallas-Fort Worth-Arlington", 0, 24),
    ("AZ", "Maricopa County", "Phoenix-Mesa-Chandler", 0, 44),
    ("AZ", "Pima County", "Tucson", 0, 19),
    ("CO", "Denver County", "Denver-Aurora-Lakewood", 0, 33),
    ("CO", "El Paso County", "Colorado Springs", 0, 21),
    ("CO", "Jefferson County", "Denver-Aurora-Lakewood", 0, 18),
    ("NC", "Mecklenburg County", "Charlotte-Concord-Gastonia", 0, 35),
    ("NC", "Wake County", "Raleigh-Cary", 0, 30),
    ("NC", "Guilford County", "Greensboro-High Point", 0, 16),
    ("UT", "Salt Lake County", "Salt Lake City", 0, 26),
    ("UT", "Utah County", "Provo-Orem", 0, 17),
    ("NV", "Clark County", "Las Vegas-Henderson-Paradise", 0, 29),
    ("OK", "Oklahoma County", "Oklahoma City", 0, 18),
    ("OK", "Tulsa County", "Tulsa", 0, 15),
    # --- rural-heavy states (thin panels) ---
    ("MT", "Yellowstone County", "Billings", 1, 7),
    ("MT", "Gallatin County", "Bozeman", 1, 6),
    ("MT", "Flathead County", "Kalispell", 1, 5),
    ("MT", "Missoula County", "Missoula", 1, 6),
    ("WY", "Laramie County", "Cheyenne", 1, 5),
    ("WY", "Natrona County", "Casper", 1, 4),
    ("WY", "Campbell County", "Non-Metro", 1, 3),
    ("NM", "Bernalillo County", "Albuquerque", 0, 17),
    ("NM", "Santa Fe County", "Santa Fe", 1, 8),
    ("NM", "Dona Ana County", "Las Cruces", 1, 6),
    ("NM", "San Juan County", "Farmington", 1, 4),
    ("KS", "Johnson County", "Kansas City", 0, 20),
    ("KS", "Sedgwick County", "Wichita", 0, 14),
    ("KS", "Riley County", "Manhattan", 1, 5),
    ("KS", "Ellis County", "Non-Metro", 1, 3),
    ("NE", "Douglas County", "Omaha-Council Bluffs", 0, 19),
    ("NE", "Lancaster County", "Lincoln", 0, 12),
    ("NE", "Buffalo County", "Non-Metro", 1, 4),
    ("NE", "Scotts Bluff County", "Non-Metro", 1, 3),
]

# --------------------------------------------------------------------- AMCs
# coverage is by state; SLA is the contractual turnaround in days.
VENDORS = [
    ("AMC-01", "Summit Valuation Partners", ["TX", "AZ", "CO", "NV", "UT"], 7),
    ("AMC-02", "Cornerstone Appraisal Group", ["NC", "OK", "KS", "NE"], 8),
    ("AMC-03", "Frontier Valuation Services", ["MT", "WY", "NM"], 10),
    ("AMC-04", "Nationwide Appraisal Exchange", None, 9),  # None = all states
]

# A three-week outage at one vendor in month 14. Orders routed to them stall.
VENDOR_OUTAGE_ID = "AMC-02"
VENDOR_OUTAGE_START = "2025-02-10"
VENDOR_OUTAGE_DAYS = 21

# ------------------------------------------------------------------ branches
# (branch_id, name, state, region)
BRANCHES = [
    ("BR-101", "Houston Central", "TX", "South"),
    ("BR-102", "Dallas North", "TX", "South"),
    ("BR-103", "Austin", "TX", "South"),
    ("BR-104", "San Antonio", "TX", "South"),
    ("BR-201", "Phoenix Metro", "AZ", "West"),
    ("BR-202", "Tucson", "AZ", "West"),
    ("BR-203", "Las Vegas", "NV", "West"),
    ("BR-204", "Salt Lake City", "UT", "West"),
    ("BR-301", "Denver Downtown", "CO", "Mountain"),
    ("BR-302", "Colorado Springs", "CO", "Mountain"),
    ("BR-303", "Billings", "MT", "Mountain"),
    ("BR-304", "Bozeman", "MT", "Mountain"),
    ("BR-305", "Cheyenne", "WY", "Mountain"),
    ("BR-306", "Albuquerque", "NM", "Mountain"),
    ("BR-401", "Charlotte", "NC", "Southeast"),
    ("BR-402", "Raleigh", "NC", "Southeast"),
    ("BR-501", "Kansas City", "KS", "Midwest"),
    ("BR-502", "Wichita", "KS", "Midwest"),
    ("BR-503", "Omaha", "NE", "Midwest"),
    ("BR-504", "Oklahoma City", "OK", "Midwest"),
]

# The branch with the data-entry quirk: DTI entered as a decimal (0.42) rather
# than a percentage (42) for a stretch of months.
DTI_QUIRK_BRANCH = "BR-502"
DTI_QUIRK_START = "2025-03-01"
DTI_QUIRK_END = "2025-07-31"

# --------------------------------------------------------------- messiness
NULL_RATE_RETAIL = 0.008
NULL_RATE_WHOLESALE = 0.041     # wholesale data capture is genuinely worse
NULL_RATE_CORRESPONDENT = 0.019
DUPLICATE_APPLICATION_COUNT = 40
OUT_OF_SEQUENCE_COUNT = 55

# ------------------------------------------------------------------ MSR model
SERVICING_RETAINED_SHARE = 0.62

# Prepay S-curve. CPR as a function of rate incentive (note rate - market rate).
CPR_FLOOR = 0.055               # turnover-driven baseline
CPR_CEILING = 0.42
CPR_STEEPNESS = 2.2
CPR_MIDPOINT = 0.95             # incentive (in %) at the curve's inflection

# Burnout: borrowers who failed to refi when deeply in the money are less
# rate-sensitive on subsequent opportunities.
BURNOUT_DECAY = 0.055

# MSR multiple anchors (as a multiple of UPB).
MSR_MULTIPLE_BASE = 4.15
MSR_MULTIPLE_FLOOR = 2.05
MSR_MULTIPLE_CEILING = 5.40

# Annual servicing cost per loan by delinquency status.
SERVICING_COST_BASE = 92.0
SERVICING_COST_MULTIPLIER = {
    "Current": 1.0,
    "30 DPD": 3.4,
    "60 DPD": 5.1,
    "90+ DPD": 8.6,
    "Foreclosure": 11.5,
    "REO": 13.0,
    "Liquidated": 0.0,
}

# Cost drifts up with inflation and spikes with portfolio-wide DQ.
SERVICING_COST_ANNUAL_DRIFT = 0.034
SERVICING_COST_DQ_SENSITIVITY = 1.8

# Float income: earned on escrow and payment balances, so it scales with the
# short rate. This is why rising rates help MSR value twice over.
FLOAT_SPREAD_TO_MARKET = 0.72
ANCILLARY_INCOME_PER_LOAN_ANNUAL = 21.0

# Roll-rate transition matrix (monthly, baseline for a prime borrower).
# Calibrated so a seasoned prime book settles near ~2-3% total delinquency and
# well under 1% in foreclosure -- a normal book, not a crisis book. Cure rates
# out of early-stage DQ are high, which is what actually happens: most 30-day
# delinquencies are administrative, not distress.
ROLL_RATES = {
    "Current":     {"Current": 0.9955, "30 DPD": 0.0045},
    "30 DPD":      {"Current": 0.7100, "30 DPD": 0.1100, "60 DPD": 0.1800},
    "60 DPD":      {"Current": 0.3100, "30 DPD": 0.1900, "60 DPD": 0.1600, "90+ DPD": 0.3400},
    "90+ DPD":     {"Current": 0.1400, "30 DPD": 0.0600, "90+ DPD": 0.7100, "Foreclosure": 0.0900},
    "Foreclosure": {"Foreclosure": 0.8800, "Current": 0.0500, "REO": 0.0700},
    # REO resolves: the property is sold and the loan leaves the portfolio.
    "REO":         {"REO": 0.7800, "Liquidated": 0.2200},
}

DQ_STATUSES = ["Current", "30 DPD", "60 DPD", "90+ DPD", "Foreclosure", "REO", "Liquidated"]

# Risk multipliers are capped so a subprime-ish file cannot be driven to an
# absurd monthly default probability by compounding adjustments.
MAX_CREDIT_RISK_MULTIPLIER = 4.5

# Rate shock buckets for the interactive what-if parameter.
RATE_SHOCK_BUCKETS = [-200, -150, -100, -50, 0, 50, 100, 150, 200]

# --------------------------------------------------------------- post-closing
TRAILING_DOC_STATUSES = [
    "Complete",
    "Pending Title Policy",
    "Pending Final Docs",
    "Pending MI Certificate",
    "Exception",
]

SUSPENSE_REASONS = [
    "Missing Note Endorsement",
    "Missing Title Policy",
    "Incorrect Assignment",
    "Missing Flood Certification",
    "Signature Discrepancy",
    "Missing MI Certificate",
    "Funding Discrepancy",
]

US_HOLIDAYS = [
    # 2024
    "2024-01-01", "2024-01-15", "2024-02-19", "2024-05-27", "2024-06-19",
    "2024-07-04", "2024-09-02", "2024-10-14", "2024-11-11", "2024-11-28",
    "2024-12-25",
    # 2025
    "2025-01-01", "2025-01-20", "2025-02-17", "2025-05-26", "2025-06-19",
    "2025-07-04", "2025-09-01", "2025-10-13", "2025-11-11", "2025-11-27",
    "2025-12-25",
    # 2026 (needed for post-closing dates that spill past the window)
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-05-25", "2026-06-19",
    "2026-07-03", "2026-09-07", "2026-10-12", "2026-11-11", "2026-11-26",
    "2026-12-25",
]


def rng(offset: int = 0) -> np.random.Generator:
    """Seeded generator. Offset lets each module draw independently."""
    return np.random.default_rng(SEED + offset)
