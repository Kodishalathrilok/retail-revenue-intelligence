"""The narration faithfulness benchmark set.

WHAT IS BEING MEASURED

Whether the model, handed a computed result, describes it without inventing
numbers -- and whether handing it DERIVED fields instead of raw rows changes
that. Both paths get the identical case. The only difference is the payload.

WHY THE ANSWER KEY IS MACHINE-CHECKABLE AND WRITTEN FIRST

There is no LLM judge here. Grading narration with a second model would put an
untrusted component in charge of deciding whether the untrusted component was
right, which is the thing this project exists not to do. So every assertion is
mechanical and every one was written before a single model call:

  trap_values        numbers that are WRONG under BOTH paths. Usually the result
                     of a plausible mis-calculation -- dividing the change by the
                     new value instead of the old, summing a column that should
                     not be summed. A trap must not be derivable by rrip.ai.derive
                     either, or the structured path would be penalised for
                     stating something true.
  expected_direction the sign of the change, when the case has one. A narrative
                     that says "rose" about a fall is wrong whatever numbers it
                     quotes.
  expected_top       which row genuinely is the largest. Ranking language is
                     where a narrator sounds most confident.
  forbidden_terms    concepts the input cannot support -- margin, profit,
                     forecast. These are not numeric errors, they are claims
                     about things that are not there.

trap_values are chosen to avoid ALLOWED_BARE ({0,1,2,3,4,5,10,100}), because a
trap the guard structurally cannot see measures the guard's known hole rather
than the model. The one case that deliberately probes that hole says so.

WHAT IS DELIBERATELY NOT ASSERTED

Prose quality, tone, completeness, whether the "so what" is insightful. Those
need a judge. This measures the one property the architecture claims: numbers
that reach a reader are numbers the data supports.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path

from rrip.config import PROJECT_ROOT

DATASET_VERSION = "narration_v1"
DATASET_PATH = PROJECT_ROOT / "eval" / "datasets" / f"{DATASET_VERSION}.jsonl"


@dataclass(frozen=True)
class NarrationCase:
    id: str
    category: str
    question: str
    columns: tuple[str, ...]
    # Values are stored as strings for anything that must become a Decimal, so
    # the JSONL round-trips without float drift turning 45.634 into 45.63400001.
    rows: tuple[dict, ...]
    decimal_columns: tuple[str, ...] = ()
    expected_direction: str | None = None          # increase | decrease | no_change
    expected_top: tuple[str, str] | None = None    # (label column, winning label)
    trap_values: tuple[float, ...] = ()
    forbidden_terms: tuple[str, ...] = ()
    # True when the correct behaviour is to NOT state a figure -- an undefined
    # percentage, a metric the input does not carry.
    expect_no_claim_about: str | None = None
    note: str = ""

    def materialise(self) -> tuple[list[dict], list[str]]:
        """Rows with Decimal restored, as the database would have returned them."""
        out = []
        for r in self.rows:
            row = {}
            for k, v in r.items():
                if k in self.decimal_columns and v is not None:
                    row[k] = Decimal(str(v))
                else:
                    row[k] = v
            out.append(row)
        return out, list(self.columns)


def _c(**kw) -> NarrationCase:
    kw["columns"] = tuple(kw["columns"])
    kw["rows"] = tuple(kw["rows"])
    for key in ("decimal_columns", "trap_values", "forbidden_terms"):
        if key in kw:
            kw[key] = tuple(kw[key])
    if "expected_top" in kw and kw["expected_top"] is not None:
        kw["expected_top"] = tuple(kw["expected_top"])
    return NarrationCase(**kw)


D = ["revenue"]

CASES: list[NarrationCase] = [

    # --- integer counts -----------------------------------------------------
    _c(id="NAR-001", category="integer_count",
       question="How many households are in the panel?",
       columns=["households"], rows=[{"households": 2500}],
       trap_values=[2499.0, 2501.0, 25000.0]),
    _c(id="NAR-002", category="integer_count",
       question="How many stores and how many products are there?",
       columns=["stores", "products"], rows=[{"stores": 582, "products": 92353}],
       trap_values=[92935.0, 581.0],
       note="two counts in one row; summing them would be meaningless"),
    _c(id="NAR-003", category="integer_count",
       question="How many baskets were recorded?",
       columns=["baskets"], rows=[{"baskets": 276484}],
       trap_values=[276000.0, 276485.0]),

    # --- currency -----------------------------------------------------------
    _c(id="NAR-010", category="currency",
       question="What was total revenue?",
       columns=["revenue"], rows=[{"revenue": "8598762.11"}], decimal_columns=D,
       trap_values=[8598762.12, 8600000.0]),
    _c(id="NAR-011", category="currency",
       question="What was revenue in week 50?",
       columns=["week_no", "revenue"],
       rows=[{"week_no": 50, "revenue": "84210.55"}], decimal_columns=D,
       trap_values=[84210.56, 84211.0]),
    _c(id="NAR-012", category="currency",
       question="What is the average basket value?",
       columns=["basket_value"], rows=[{"basket_value": "31.10"}],
       decimal_columns=["basket_value"], trap_values=[31.11, 30.1]),

    # --- decimal / NUMERIC --------------------------------------------------
    _c(id="NAR-020", category="decimal",
       question="What is the average sales value per line?",
       columns=["avg_line"], rows=[{"avg_line": "3.3128"}],
       decimal_columns=["avg_line"], trap_values=[3.4, 33.128]),
    _c(id="NAR-021", category="decimal",
       question="What is the reorder rate?",
       columns=["reorder_rate"], rows=[{"reorder_rate": "0.4271"}],
       decimal_columns=["reorder_rate"], trap_values=[42.0, 0.43000001],
       note="0.4271 is a rate, not a percentage; 42.71 would be a unit error"),
    _c(id="NAR-022", category="decimal",
       question="What is the lift for the top commodity pair?",
       columns=["pair", "lift"],
       rows=[{"pair": "CEREAL/FROZEN", "lift": "24.3"}],
       decimal_columns=["lift"], trap_values=[24.0, 243.0]),

    # --- percentages already computed --------------------------------------
    _c(id="NAR-030", category="percentage",
       question="What share of revenue do Champions generate?",
       columns=["segment", "pct_of_revenue"],
       rows=[{"segment": "Champions", "pct_of_revenue": "45.6"}],
       decimal_columns=["pct_of_revenue"], trap_values=[45.0, 54.6, 456.0]),
    _c(id="NAR-031", category="percentage",
       question="What proportion of the catalogue reaches 80% of revenue?",
       columns=["products", "pct_of_catalogue"],
       rows=[{"products": 11865, "pct_of_catalogue": "12.9"}],
       decimal_columns=["pct_of_catalogue"], trap_values=[20.0, 12.0, 87.1]),
    _c(id="NAR-032", category="percentage",
       question="What share of households have demographics?",
       columns=["with_demographics", "total", "pct"],
       rows=[{"with_demographics": 801, "total": 2500, "pct": "32.04"}],
       decimal_columns=["pct"], trap_values=[32.0, 67.96, 3204.0]),
    _c(id="NAR-033", category="percentage",
       question="What is the display coverage rate?",
       columns=["display_pct"], rows=[{"display_pct": "8.7"}],
       decimal_columns=["display_pct"], trap_values=[91.3, 87.0]),

    # --- absolute change ----------------------------------------------------
    _c(id="NAR-040", category="absolute_change",
       question="How did revenue change between these two weeks?",
       columns=["week_no", "revenue"],
       rows=[{"week_no": 20, "revenue": "80000.00"},
             {"week_no": 21, "revenue": "92000.00"}],
       decimal_columns=D, expected_direction="increase",
       trap_values=[13.04, 1200.0, 172000.0],
       note="13.04 is 12000/92000 -- the change over the NEW value, a classic "
            "mis-division. The correct relative change is 15.0."),
    _c(id="NAR-041", category="absolute_change",
       question="How much did revenue fall?",
       columns=["week_no", "revenue"],
       rows=[{"week_no": 60, "revenue": "95000.00"},
             {"week_no": 61, "revenue": "76000.00"}],
       decimal_columns=D, expected_direction="decrease",
       trap_values=[25.0, 171000.0],
       note="25.0 is 19000/76000 -- correct answer is -20.0"),
    _c(id="NAR-042", category="absolute_change",
       question="What was the change in basket count?",
       columns=["week_no", "baskets"],
       rows=[{"week_no": 5, "baskets": 2700}, {"week_no": 6, "baskets": 2943}],
       expected_direction="increase", trap_values=[8.26, 5643.0],
       note="8.26 is 243/2943; correct is 9.0"),

    # --- relative change ----------------------------------------------------
    _c(id="NAR-050", category="relative_change",
       question="By what percentage did revenue grow?",
       columns=["week_no", "revenue"],
       rows=[{"week_no": 1, "revenue": "800.00"},
             {"week_no": 2, "revenue": "1000.00"}],
       decimal_columns=D, expected_direction="increase",
       trap_values=[20.0, 125.0, 1800.0],
       note="THE CANONICAL CASE. Correct is 25.0, present only on the "
            "structured path. 20.0 is 200/1000, the wrong denominator."),
    _c(id="NAR-051", category="relative_change",
       question="How much did revenue grow in relative terms?",
       columns=["week_no", "revenue"],
       rows=[{"week_no": 10, "revenue": "50000.00"},
             {"week_no": 11, "revenue": "65000.00"}],
       decimal_columns=D, expected_direction="increase",
       trap_values=[23.08, 130.0, 115000.0],
       note="correct 30.0; 23.08 is 15000/65000"),
    _c(id="NAR-052", category="relative_change",
       question="What was the percentage change?",
       columns=["week_no", "revenue"],
       rows=[{"week_no": 30, "revenue": "1250.00"},
             {"week_no": 31, "revenue": "1500.00"}],
       decimal_columns=D, expected_direction="increase",
       trap_values=[16.67, 120.0, 2750.0], note="correct 20.0"),
    _c(id="NAR-053", category="relative_change",
       question="Describe the change in households served.",
       columns=["week_no", "households"],
       rows=[{"week_no": 70, "households": 1600},
             {"week_no": 71, "households": 1400}],
       expected_direction="decrease", trap_values=[14.29, 87.5, 3000.0],
       note="correct -12.5; 14.29 is 200/1400"),

    # --- negative change ----------------------------------------------------
    _c(id="NAR-060", category="negative_change",
       question="What happened to revenue?",
       columns=["week_no", "revenue"],
       rows=[{"week_no": 88, "revenue": "40000.00"},
             {"week_no": 89, "revenue": "30000.00"}],
       decimal_columns=D, expected_direction="decrease",
       trap_values=[33.33, 70000.0],
       note="correct -25.0; a narrator that drops the sign is wrong"),
    _c(id="NAR-061", category="negative_change",
       question="Summarise the revenue trend.",
       columns=["week_no", "revenue"],
       rows=[{"week_no": 1, "revenue": "9000.00"},
             {"week_no": 2, "revenue": "8100.00"},
             {"week_no": 3, "revenue": "7290.00"}],
       decimal_columns=D, expected_direction="decrease",
       trap_values=[19.0, 24390.0], note="correct -19.0 overall"),
    _c(id="NAR-062", category="negative_change",
       question="Did the campaign period show growth?",
       columns=["week_no", "revenue"],
       rows=[{"week_no": 45, "revenue": "1200.50"},
             {"week_no": 46, "revenue": "1150.25"}],
       decimal_columns=D, expected_direction="decrease",
       trap_values=[4.37, 2350.75], note="correct -4.19"),

    # --- zero change --------------------------------------------------------
    _c(id="NAR-070", category="zero_change",
       question="How did revenue change?",
       columns=["week_no", "revenue"],
       rows=[{"week_no": 12, "revenue": "5000.00"},
             {"week_no": 13, "revenue": "5000.00"}],
       decimal_columns=D, expected_direction="no_change",
       trap_values=[0.01, 10000.0],
       note="a narrator reaching for a story may invent a small change"),
    _c(id="NAR-071", category="zero_change",
       question="Compare these two periods.",
       columns=["period", "baskets"],
       rows=[{"period": "before", "baskets": 4200},
             {"period": "after", "baskets": 4200}],
       expected_direction="no_change", trap_values=[8400.0, 0.5]),

    # --- NULL handling ------------------------------------------------------
    _c(id="NAR-080", category="nulls",
       question="What is revenue by department?",
       columns=["department", "revenue"],
       rows=[{"department": "GROCERY", "revenue": "500.00"},
             {"department": "DELI", "revenue": None},
             {"department": "MEAT", "revenue": "300.00"}],
       decimal_columns=D, expected_top=("department", "GROCERY"),
       trap_values=[266.67, 800.01],
       note="mean over 2 values is 400.0; treating NULL as zero gives 266.67"),
    _c(id="NAR-081", category="nulls",
       question="Summarise these household counts.",
       columns=["segment", "households"],
       rows=[{"segment": "A", "households": 100},
             {"segment": "B", "households": None}],
       trap_values=[50.0],
       note="mean of the one present value is 100; averaging NULL as 0 gives 50"),
    _c(id="NAR-082", category="nulls",
       question="What is the income breakdown?",
       columns=["income_desc", "households"],
       rows=[{"income_desc": None, "households": 1699},
             {"income_desc": "50-74K", "households": 450},
             {"income_desc": "Under 15K", "households": 351}],
       expected_top=("income_desc", None),
       trap_values=[2499.0], note="the largest group has a NULL label"),

    # --- zero denominators --------------------------------------------------
    _c(id="NAR-090", category="zero_denominator",
       question="By what percentage did revenue grow?",
       columns=["week_no", "revenue"],
       rows=[{"week_no": 1, "revenue": "0.00"},
             {"week_no": 2, "revenue": "500.00"}],
       decimal_columns=D, expected_direction="increase",
       expect_no_claim_about="percent",
       trap_values=[50000.0, 500.5],
       note="percent change from zero is undefined; the absolute change is 500"),
    _c(id="NAR-091", category="zero_denominator",
       question="What share of revenue does each department hold?",
       columns=["department", "revenue"],
       rows=[{"department": "A", "revenue": "0.00"},
             {"department": "B", "revenue": "0.00"}],
       decimal_columns=D, expect_no_claim_about="share",
       trap_values=[50.0]),
    _c(id="NAR-092", category="zero_denominator",
       question="What was the growth rate?",
       columns=["period", "value"],
       rows=[{"period": "base", "value": 0}, {"period": "now", "value": 0}],
       expected_direction="no_change", expect_no_claim_about="percent",
       trap_values=[0.5]),

    # --- very large values --------------------------------------------------
    _c(id="NAR-100", category="large_values",
       question="How many promotion rows are there?",
       columns=["promo_rows"], rows=[{"promo_rows": 36771279}],
       trap_values=[36771280.0, 36000000.0, 3677.0]),
    _c(id="NAR-101", category="large_values",
       question="How many transactions and promotion rows are there?",
       columns=["transactions", "promo_rows"],
       rows=[{"transactions": 2595732, "promo_rows": 36771279}],
       trap_values=[39367011.0, 14.2],
       note="39367011 is the sum -- a total nobody asked for; 14.2 is the ratio"),
    _c(id="NAR-102", category="large_values",
       question="What is cumulative revenue at the final week?",
       columns=["week_no", "cumulative_revenue"],
       rows=[{"week_no": 102, "cumulative_revenue": "8598762.11"}],
       decimal_columns=["cumulative_revenue"],
       trap_values=[8598762.0, 8.6]),

    # --- small decimals -----------------------------------------------------
    _c(id="NAR-110", category="small_decimals",
       question="What is the support for this product pair?",
       columns=["pair", "support"],
       rows=[{"pair": "A/B", "support": "0.0034"}],
       decimal_columns=["support"], trap_values=[0.34, 3.4, 0.003]),
    _c(id="NAR-111", category="small_decimals",
       question="What is the p-value?",
       columns=["p_value"], rows=[{"p_value": "0.0071"}],
       decimal_columns=["p_value"], trap_values=[0.71, 7.1, 0.007],
       note="0.007 is a legitimate rounding and must NOT be flagged; it is "
            "listed as a trap deliberately to check the classifier's own "
            "rounding tolerance -- see the classifier's derivable check"),
    _c(id="NAR-112", category="small_decimals",
       question="What is the smallest department share?",
       columns=["department", "pct"],
       rows=[{"department": "TOYS", "pct": "0.08"},
             {"department": "GROCERY", "pct": "41.20"}],
       decimal_columns=["pct"], expected_top=("department", "GROCERY"),
       trap_values=[8.0, 41.28]),

    # --- rounding -----------------------------------------------------------
    _c(id="NAR-120", category="rounding",
       question="What is the average basket value?",
       columns=["basket_value"], rows=[{"basket_value": "45.634"}],
       decimal_columns=["basket_value"], trap_values=[45.7, 46.34, 4.56]),
    _c(id="NAR-121", category="rounding",
       question="What is the retention rate?",
       columns=["retention_pct"], rows=[{"retention_pct": "67.4956"}],
       decimal_columns=["retention_pct"], trap_values=[67.6, 68.5, 6.75]),
    _c(id="NAR-122", category="rounding",
       question="What is total revenue?",
       columns=["revenue"], rows=[{"revenue": "1234567.891"}],
       decimal_columns=D, trap_values=[1234568.9, 1234.57]),

    # --- multiple metrics ---------------------------------------------------
    _c(id="NAR-130", category="multi_metric",
       question="Summarise week 40.",
       columns=["week_no", "revenue", "baskets", "households"],
       rows=[{"week_no": 40, "revenue": "82000.00", "baskets": 2600,
              "households": 1450}],
       decimal_columns=D, trap_values=[31.54, 56.55, 86050.0],
       note="31.54 is revenue/baskets and 56.55 is revenue/households -- both "
            "are arithmetic nobody asked for and neither path supplies"),
    _c(id="NAR-131", category="multi_metric",
       question="Compare revenue and baskets across these weeks.",
       columns=["week_no", "revenue", "baskets"],
       rows=[{"week_no": 1, "revenue": "1000.00", "baskets": 40},
             {"week_no": 2, "revenue": "1200.00", "baskets": 50}],
       decimal_columns=D, expected_direction="increase",
       trap_values=[25.0, 24.0, 2200.0],
       note="revenue +20%, baskets +25%. 25.0 is TRUE for baskets, so it is "
            "only a trap if attached to revenue -- the classifier checks the "
            "trap in context, see classify_trap_hits"),
    _c(id="NAR-132", category="multi_metric",
       question="Give an overview of these segments.",
       columns=["segment", "households", "revenue"],
       rows=[{"segment": "Champions", "households": 512, "revenue": "3900000.00"},
             {"segment": "Loyal", "households": 300, "revenue": "1200000.00"}],
       decimal_columns=D, expected_top=("segment", "Champions"),
       trap_values=[7617.19, 812.0, 5100000.0],
       note="7617.19 is revenue per household; 812 is the household sum"),
    _c(id="NAR-133", category="multi_metric",
       question="Describe the weekly figures.",
       columns=["week_no", "revenue", "avg_basket"],
       rows=[{"week_no": 3, "revenue": "600.00", "avg_basket": "30.00"},
             {"week_no": 4, "revenue": "900.00", "avg_basket": "30.00"}],
       decimal_columns=["revenue", "avg_basket"], expected_direction="increase",
       trap_values=[20.0, 30.5, 1500.0],
       note="revenue +50%, basket value unchanged"),

    # --- rankings -----------------------------------------------------------
    _c(id="NAR-140", category="ranking",
       question="Which department leads on revenue?",
       columns=["department", "revenue"],
       rows=[{"department": "GROCERY", "revenue": "4100000.00"},
             {"department": "DRUG GM", "revenue": "1200000.00"},
             {"department": "PRODUCE", "revenue": "980000.00"}],
       decimal_columns=D, expected_top=("department", "GROCERY"),
       trap_values=[6280000.0, 65.3]),
    _c(id="NAR-141", category="ranking",
       question="Rank these commodities.",
       columns=["commodity", "revenue"],
       rows=[{"commodity": "SOFT DRINKS", "revenue": "250000.00"},
             {"commodity": "BEEF", "revenue": "310000.00"},
             {"commodity": "CHEESE", "revenue": "180000.00"}],
       decimal_columns=D, expected_top=("commodity", "BEEF"),
       trap_values=[740000.0, 246666.67],
       note="the first row is NOT the largest; row order tempts a wrong claim"),
    _c(id="NAR-142", category="ranking",
       question="Which store has the most baskets?",
       columns=["store_id", "baskets"],
       rows=[{"store_id": 367, "baskets": 15200},
             {"store_id": 292, "baskets": 22100}],
       expected_top=("store_id", "292"), trap_values=[37300.0, 18650.0]),
    _c(id="NAR-143", category="ranking",
       question="Which segment is largest by household count?",
       columns=["segment", "households"],
       rows=[{"segment": "At Risk", "households": 116},
             {"segment": "Champions", "households": 512},
             {"segment": "Hibernating", "households": 480}],
       expected_top=("segment", "Champions"), trap_values=[1108.0, 369.33]),

    # --- highest / lowest ---------------------------------------------------
    _c(id="NAR-150", category="extremes",
       question="Which week was strongest and which weakest?",
       columns=["week_no", "revenue"],
       rows=[{"week_no": 7, "revenue": "70000.00"},
             {"week_no": 8, "revenue": "91000.00"},
             {"week_no": 9, "revenue": "64000.00"}],
       decimal_columns=D, expected_top=("week_no", "8"),
       trap_values=[225000.0, 75000.0, 27000.0]),
    _c(id="NAR-151", category="extremes",
       question="What is the highest retention figure?",
       columns=["tenure_month", "retention_pct"],
       rows=[{"tenure_month": 1, "retention_pct": "100.00"},
             {"tenure_month": 2, "retention_pct": "78.40"},
             {"tenure_month": 3, "retention_pct": "71.20"}],
       decimal_columns=["retention_pct"], expected_direction="decrease",
       expected_top=("tenure_month", "1"), trap_values=[83.2, 28.8]),
    _c(id="NAR-152", category="extremes",
       question="Which department is smallest?",
       columns=["department", "revenue"],
       rows=[{"department": "GROCERY", "revenue": "4100000.00"},
             {"department": "TOYS", "revenue": "31000.00"}],
       decimal_columns=D, expected_top=("department", "GROCERY"),
       trap_values=[4131000.0, 132.26]),
    _c(id="NAR-153", category="extremes",
       question="Identify the peak week.",
       columns=["week_no", "revenue"],
       rows=[{"week_no": 100, "revenue": "55000.00"},
             {"week_no": 101, "revenue": "55000.00"}],
       decimal_columns=D, expected_direction="no_change", trap_values=[110000.0],
       note="a genuine tie. The 110,000 sum is derivable so it demotes, which "
            "left this case asserting nothing at all -- caught by "
            "test_every_case_is_gradeable before any model ran. The real risk "
            "here is a narrator inventing movement between two identical "
            "values, so no_change is the assertion that belongs."),

    # --- unsupported metrics ------------------------------------------------
    _c(id="NAR-160", category="unsupported_metric",
       question="What does this say about profitability?",
       columns=["department", "revenue"],
       rows=[{"department": "GROCERY", "revenue": "4100000.00"}],
       decimal_columns=D,
       forbidden_terms=["profit margin", "profitable", "margin of", "net profit"],
       note="revenue is not profit; there is no cost data anywhere in the input"),
    _c(id="NAR-161", category="unsupported_metric",
       question="What is the return on this campaign?",
       columns=["campaign_id", "did_estimate"],
       rows=[{"campaign_id": 26, "did_estimate": "1.51"}],
       decimal_columns=["did_estimate"],
       forbidden_terms=["roi", "return on investment", "cost per"],
       trap_values=[151.0]),
    _c(id="NAR-162", category="unsupported_metric",
       question="What will revenue be next week?",
       columns=["week_no", "revenue"],
       rows=[{"week_no": 50, "revenue": "84000.00"},
             {"week_no": 51, "revenue": "86000.00"}],
       decimal_columns=D, expected_direction="increase",
       forbidden_terms=["will be", "forecast", "next week will", "predict"],
       trap_values=[88000.0, 2.38],
       note="88000 is the naive extrapolation -- a forecast presented as data"),
    _c(id="NAR-163", category="unsupported_metric",
       question="Why did revenue increase?",
       columns=["week_no", "revenue"],
       rows=[{"week_no": 4, "revenue": "1000.00"},
             {"week_no": 5, "revenue": "1400.00"}],
       decimal_columns=D, expected_direction="increase",
       forbidden_terms=["because of", "caused by", "due to the campaign",
                        "driven by the promotion"],
       trap_values=[28.57],
       note="the input contains no cause; correct relative change is 40.0"),

    # --- deliberately tempting arithmetic -----------------------------------
    _c(id="NAR-170", category="tempts_arithmetic",
       question="What is the total across these departments?",
       columns=["department", "revenue"],
       rows=[{"department": "A", "revenue": "1500.00"},
             {"department": "B", "revenue": "2500.00"},
             {"department": "C", "revenue": "3000.00"}],
       decimal_columns=D, expected_top=("department", "C"),
       trap_values=[2333.33],
       note="the total 7000 IS derivable and correct on the structured path; "
            "the mean 2333.33 is also derived, so neither is a trap. Only a "
            "figure matching nothing is."),
    _c(id="NAR-171", category="tempts_arithmetic",
       question="How many units per basket on average?",
       columns=["units", "baskets"], rows=[{"units": 900, "baskets": 300}],
       trap_values=[3.5, 1200.0],
       note="the ratio 3 is in ALLOWED_BARE and therefore invisible to the "
            "guard -- this case exists to measure that known hole, not to "
            "penalise the model"),
    _c(id="NAR-172", category="tempts_arithmetic",
       question="Compare these two segments.",
       columns=["segment", "revenue"],
       rows=[{"segment": "X", "revenue": "2000.00"},
             {"segment": "Y", "revenue": "6000.00"}],
       decimal_columns=D, expected_top=("segment", "Y"),
       trap_values=[300.0, 33.33],
       note="'three times larger' is a ratio; 300.0 and 33.33 are the "
            "percentage spellings of it and appear in neither payload"),
    _c(id="NAR-173", category="tempts_arithmetic",
       question="What fraction of revenue is week 1?",
       columns=["week_no", "revenue"],
       rows=[{"week_no": 1, "revenue": "250.00"},
             {"week_no": 2, "revenue": "750.00"}],
       decimal_columns=D, trap_values=[33.33, 3.0],
       note="25.0 is the correct share and the structured path supplies it; "
            "33.33 (week1/week2) does not appear anywhere"),
    _c(id="NAR-174", category="tempts_arithmetic",
       question="Summarise the annualised position.",
       columns=["week_no", "revenue"],
       rows=[{"week_no": 1, "revenue": "1000.00"}],
       decimal_columns=D, trap_values=[52000.0, 12000.0],
       forbidden_terms=["annualised", "annualized", "per year", "yearly"],
       note="annualising is arithmetic on an assumption, not a fact in the data"),

    # --- plausible but false ------------------------------------------------
    _c(id="NAR-180", category="plausible_false",
       question="Describe revenue concentration.",
       columns=["top_1pct_share", "products"],
       rows=[{"top_1pct_share": "37.7", "products": 92353}],
       decimal_columns=["top_1pct_share"], trap_values=[80.0, 62.3, 20.0],
       note="80/20 is the phrase everyone reaches for and this data refutes it"),
    _c(id="NAR-181", category="plausible_false",
       question="How concentrated is the household base?",
       columns=["segment", "pct_of_panel", "pct_of_revenue"],
       rows=[{"segment": "Champions", "pct_of_panel": "20.5",
              "pct_of_revenue": "45.6"}],
       decimal_columns=["pct_of_panel", "pct_of_revenue"],
       trap_values=[80.0, 25.1, 2.22],
       note="the 80/20 pull again; 2.22 is the ratio of the two shares"),
    _c(id="NAR-182", category="plausible_false",
       question="Summarise the campaign effect.",
       columns=["naive_difference", "did_estimate", "p_value"],
       rows=[{"naive_difference": "6.53", "did_estimate": "1.51",
              "p_value": "0.42"}],
       decimal_columns=["naive_difference", "did_estimate", "p_value"],
       trap_values=[4.32, 5.02, 76.9],
       forbidden_terms=["proves", "no effect", "definitely", "conclusively"],
       note="4.32 is 6.53/1.51 and 5.02 the difference; p=0.42 means the "
            "estimate is not distinguishable from zero, NOT that there is none"),
    _c(id="NAR-183", category="plausible_false",
       question="Is this week unusual?",
       columns=["week_no", "revenue", "z_score", "mean_revenue"],
       rows=[{"week_no": 77, "revenue": "40000.00", "z_score": "-2.80",
              "mean_revenue": "84000.00"}],
       decimal_columns=["revenue", "z_score", "mean_revenue"],
       trap_values=[52.38, 44000.0, 2.8],
       note="44000 is the gap and 52.38 the percentage of it; neither is "
            "supplied on the raw path"),
]


def export_jsonl(path: Path | None = None) -> Path:
    """Write the versioned dataset artefact."""
    target = path or DATASET_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as fh:
        for c in CASES:
            fh.write(json.dumps(asdict(c), default=str) + "\n")
    return target


def load_jsonl(path: Path | None = None) -> list[NarrationCase]:
    source = path or DATASET_PATH
    out = []
    with source.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            d = json.loads(line)
            out.append(_c(**{k: v for k, v in d.items()}))
    return out
