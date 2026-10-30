"""2025 tax tables. Federal and Alberta are copied exactly from owner_ab_2025.py (the reference).

Adding a year or province = adding an entry here and (for a year) to TAX_YEARS and CAPITAL_GAINS_INCLUSION.
"""
from __future__ import annotations

from .core import BPA, Brackets, DividendRates, Jurisdiction, LowIncomeReduction, INF

TAX_YEARS = (2025,)
CAPITAL_GAINS_INCLUSION = {2025: 0.50}  # owner file: flat 50% for 2025

_GROSS_UP = {"eligible": 0.38, "non_eligible": 0.15}  # owner file; CRA gross-up rates, same federally and in BC/AB

FEDERAL = {2025: Jurisdiction(
    name="Federal", year=2025,
    brackets=Brackets(((0, 57_375, 0.145), (57_375, 114_750, 0.205), (114_750, 177_882, 0.26),
                       (177_882, 253_414, 0.29), (253_414, INF, 0.33))),
    bpa=BPA(16_129.0, 14_538.0, 177_882.0, 253_414.0),
    lowest_rate=0.145,  # blended 15% Jan-Jun / 14% Jul-Dec (owner file)
    dtc=DividendRates(_GROSS_UP, {"eligible": 0.150198, "non_eligible": 0.090301}),
)}

ALBERTA = {2025: Jurisdiction(
    name="Alberta", year=2025,
    brackets=Brackets(((0, 60_000, 0.08), (60_000, 151_234, 0.10), (151_234, 181_481, 0.12),
                       (181_481, 241_974, 0.13), (241_974, 362_961, 0.14), (362_961, INF, 0.15))),
    bpa=BPA(22_323.0),
    lowest_rate=0.08,
    dtc=DividendRates(_GROSS_UP, {"eligible": 0.0812, "non_eligible": 0.0218}),
)}

BRITISH_COLUMBIA = {2025: Jurisdiction(
    name="British Columbia", year=2025,
    # Brackets: https://www.wealthsimple.com/en-ca/learn/bc-tax-brackets ; https://www.taxtips.ca/taxrates/bc.htm
    #   (search-snippet agreement from wealthsimple, calctax, paymappr; BC indexation factor 1.028)
    brackets=Brackets(((0, 49_279, 0.0506), (49_279, 98_560, 0.077), (98_560, 113_158, 0.105),
                       (113_158, 137_407, 0.1229), (137_407, 186_306, 0.147), (186_306, 259_829, 0.168),
                       (259_829, INF, 0.205))),
    # BPA $12,932: https://www2.gov.bc.ca/gov/content/taxes/income-taxes/personal/credits/basic (2025 row)
    bpa=BPA(12_932.0),
    lowest_rate=0.0506,
    # DTC (% of grossed-up amount): https://www2.gov.bc.ca/gov/content/taxes/income-taxes/personal/credits/basic
    #   eligible 12%, non-eligible 1.96% for 2025. EY (https://taxnews.ey.com/news/2025-0615-canada-british-columbia-budget-2025-26)
    #   mentions a cut of non-eligible DTC to 1.5% tied to the small-business rate cut: UNVERIFIED which year it applies.
    dtc=DividendRates(_GROSS_UP, {"eligible": 0.12, "non_eligible": 0.0196}),
    # Tax reduction credit 2025: base $562, threshold $25,020, rate 3.56%, zero at $40,807
    #   https://www2.gov.bc.ca/gov/content/taxes/income-taxes/personal/credits/basic ; https://www.taxtips.ca/bctax/personal-tax-credits.htm
    # (2026 values are $690 / $25,570; do not mix.) Applied against net income ~ taxable income here.
    low_income_reduction=LowIncomeReduction(base=562.0, threshold=25_020.0, rate=0.0356),
    # No surtax, no health premium: MSP premiums ended 2020 (replaced by Employer Health Tax); no BC surtax.
    # UNVERIFIED by search this session (stated from knowledge only).
    surtax=None, health_premium=None,
)}

JURISDICTIONS = {"AB": ALBERTA, "BC": BRITISH_COLUMBIA}
