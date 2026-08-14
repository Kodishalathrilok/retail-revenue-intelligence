"""Predictive intent routing, and the boundary the LLM may not cross.

The failure being guarded against: asked "what will Grocery revenue be next
week?" with only a SQL tool available, a language model writes a valid
aggregate over historical rows and returns a number that passes every gate this
project has and is not a forecast. Nothing downstream can tell it apart from a
real one.
"""

from __future__ import annotations

import pytest

from rrip.ai import forecast_intent as FI
from rrip.ai.router import (
    AMBIGUOUS,
    ANSWERABLE,
    FORECAST,
    UNSAFE,
    UNSUPPORTED,
    classify,
)
from rrip.eval.cases import CASES, CORRECT
from rrip.forecast.contract import HORIZON_WEEKS

DEPARTMENTS = [
    "COSMETICS", "DELI", "DRUG GM", "FLORAL", "GARDEN CENTER", "GROCERY",
    "KIOSK-GAS", "MEAT", "MEAT-PCKGD", "NUTRITION", "PASTRY", "PRODUCE",
    "SEAFOOD", "SEAFOOD-PCKGD", "SPIRITS",
]


# --- the router must not regress on existing behaviour ----------------------

@pytest.mark.parametrize("case", [c for c in CASES if c.expectation == CORRECT],
                         ids=lambda c: c.id)
def test_adding_forecast_routing_blocks_no_answerable_case(case):
    """The false-positive guard, extended to the new verdict.

    A new verdict that captures descriptive questions would silently narrow the
    system, and every benchmark case with a reference answer is the evidence
    that it has not.
    """
    routing = classify(case.question)
    assert routing.verdict == ANSWERABLE, (
        f"{case.id} now routes {routing.verdict} via rule {routing.rule!r} "
        f"(matched {routing.matched}) but it has a reference answer")


PREDICTIVE = [
    "What is next week's expected Grocery revenue?",
    "Forecast revenue for the produce department.",
    "How much will the meat department make next week?",
    "What is the projected revenue for DRUG GM?",
    "Predict next week's revenue for pastry.",
    "What is the outlook for deli revenue?",
    "What will revenue be in the coming week for seafood?",
]

DESCRIPTIVE = [
    "What is the total revenue by department?",
    "Show revenue per week for the first 20 weeks.",
    "Which departments show the biggest drop in revenue between weeks?",
    "How many baskets did household 100 have?",
    "What is the average basket value in week 50?",
    "Which 10 commodities have the highest units sold?",
]


@pytest.mark.parametrize("q", PREDICTIVE, ids=lambda q: q[:40])
def test_predictive_questions_route_to_forecast(q):
    r = classify(q)
    assert r.verdict == FORECAST, f"{q!r} routed {r.verdict}"
    assert r.rule == "forecast_intent"
    assert r.should_forecast and not r.should_generate_sql


@pytest.mark.parametrize("q", DESCRIPTIVE, ids=lambda q: q[:40])
def test_descriptive_questions_do_not_route_to_forecast(q):
    r = classify(q)
    assert r.verdict == ANSWERABLE, f"{q!r} routed {r.verdict} ({r.rule})"


# --- scope: only revenue is forecastable ------------------------------------

@pytest.mark.parametrize("q", [
    "Predict how many units we will sell next week.",
    "Forecast the number of baskets next week.",
    "How many active households will we have next week?",
    "What will the quantity sold be next week?",
])
def test_predictive_questions_about_unmodelled_metrics_are_refused(q):
    r = classify(q)
    assert r.verdict == UNSUPPORTED
    assert r.rule == "forecast_scope"
    assert r.clarification


@pytest.mark.parametrize("q,rule", [
    ("Will customer satisfaction improve next week?", "sentiment"),
    ("What will our profit margin be next week?", "cost_and_margin"),
    ("Predict next week's revenue for our competitors.", "competitor"),
    ("Forecast stock levels for next week.", "inventory"),
])
def test_absent_domains_win_over_forecast_routing(q, rule):
    """Refuse for the reason that applies, not by dispatching to a forecaster
    that would have to refuse it again."""
    r = classify(q)
    assert r.verdict == UNSUPPORTED
    assert r.rule == rule


def test_unsafe_still_wins_over_everything():
    r = classify("Delete all the transaction records and forecast next week.")
    assert r.verdict == UNSAFE


def test_a_forecast_question_is_not_treated_as_ambiguous():
    """The forecaster has one target, so there is no choice to clarify."""
    r = classify("What is next week's expected Grocery revenue?")
    assert r.verdict == FORECAST
    assert r.verdict != AMBIGUOUS


# --- parameter extraction ---------------------------------------------------

@pytest.mark.parametrize("question,expected", [
    ("What is next week's expected Grocery revenue?", "GROCERY"),
    ("Forecast revenue for the produce department.", "PRODUCE"),
    ("Predict the fuel department's revenue.", "KIOSK-GAS"),
    ("What is the outlook for bakery revenue?", "PASTRY"),
    ("Forecast revenue for MEAT-PCKGD.", "MEAT-PCKGD"),
    ("How much will flowers make next week?", "FLORAL"),
    ("Forecast liquor revenue next week.", "SPIRITS"),
])
def test_department_extraction(question, expected):
    assert FI.resolve(question, DEPARTMENTS).department == expected


@pytest.mark.parametrize("question,expected", [
    ("Forecast revenue for MEAT-PCKGD.", "MEAT-PCKGD"),
    ("What is next week's expected Grocery revenue?", "GROCERY"),
    ("Forecast DRUG GM revenue!", "DRUG GM"),
    ("produce, please", "PRODUCE"),
])
def test_trailing_punctuation_does_not_defeat_a_department_match(question,
                                                                 expected):
    """REGRESSION: a full stop after a department name broke the match.

    Department names contain '.', '-', '/' and '&', so punctuation cannot be
    stripped wholesale and the match cannot require surrounding spaces. The
    same class of bug the router carries a test for.
    """
    assert FI.resolve(question, DEPARTMENTS).department == expected


def test_longest_match_wins_across_names_and_aliases():
    """REGRESSION: 'meat' is a substring of 'packaged meat'.

    The exact-name pass once ran first and returned MEAT for a question about
    packaged meat. They are different series with different forecasts, and
    nothing in the answer would have looked wrong.
    """
    assert FI.resolve("how much will packaged meat make next week",
                      DEPARTMENTS).department == "MEAT-PCKGD"
    assert FI.resolve("what will meat revenue be next week",
                      DEPARTMENTS).department == "MEAT"


def test_a_department_outside_the_model_is_distinguished_from_none():
    intent = FI.resolve("forecast revenue for the restaurant", DEPARTMENTS)
    assert intent.department is None
    assert intent.unknown_department == "RESTAURANT"

    nothing = FI.resolve("forecast next week's revenue", DEPARTMENTS)
    assert nothing.department is None
    assert nothing.unknown_department is None


@pytest.mark.parametrize("question,weeks", [
    ("What is next week's expected Grocery revenue?", 1),
    ("Forecast Grocery revenue for the coming week.", 1),
    ("Forecast Grocery revenue 3 weeks ahead.", 3),
    ("Forecast Grocery revenue in 8 weeks.", 8),
    ("Forecast Grocery revenue.", HORIZON_WEEKS),
])
def test_horizon_extraction_is_faithful_not_clamped(question, weeks):
    """An unsupported horizon is extracted as asked and refused downstream.

    Clamping it to 1 would answer a different question under this one's label.
    """
    assert FI.extract_horizon(question) == weeks


def test_intent_carries_no_figures():
    """The structural guarantee: intent is parameters, never predictions."""
    intent = FI.resolve("What is next week's expected Grocery revenue?",
                        DEPARTMENTS)
    d = intent.to_dict()
    assert set(d) == {"intent", "department", "horizon", "resolved_by",
                      "matched_text", "unknown_department",
                      "supported_horizons"}
    assert d["intent"] == "forecast"
    assert "prediction" not in d and "revenue" not in d


def test_every_alias_target_is_a_real_dunnhumby_department():
    """An alias pointing at a name that does not exist can never resolve."""
    real = {
        "GROCERY", "DRUG GM", "PRODUCE", "MEAT", "MEAT-PCKGD", "KIOSK-GAS",
        "DELI", "PASTRY", "SEAFOOD", "SEAFOOD-PCKGD", "FLORAL", "COSMETICS",
        "NUTRITION", "SPIRITS", "SALAD BAR", "GARDEN CENTER", "RESTAURANT",
    }
    unknown = {t for t in FI.ALIASES.values() if t not in real}
    assert not unknown, f"aliases point at non-existent departments: {unknown}"


# --- the LLM boundary -------------------------------------------------------

class _StubProvider:
    """Returns whatever it is told to, so validation can be tested."""

    def __init__(self, reply: str):
        self.reply = reply
        self.available = True
        self.calls = 0

    async def complete(self, prompt, system="", **kw):
        self.calls += 1
        from rrip.ai.provider import LLMResponse
        return LLMResponse(self.reply, "stub", "stub")


@pytest.mark.anyio
async def test_llm_output_outside_the_department_list_is_discarded():
    p = _StubProvider("BAKERY DEPARTMENT (probably)")
    intent = await FI.resolve_with_llm("what about the bread aisle next week",
                                       DEPARTMENTS, p)
    assert intent.department is None, (
        "a name outside the closed list must be discarded, not trusted")


@pytest.mark.anyio
async def test_llm_cannot_smuggle_a_number_through():
    p = _StubProvider("GROCERY will be $52,000")
    intent = await FI.resolve_with_llm("forecast the big aisle", DEPARTMENTS, p)
    assert intent.department is None


@pytest.mark.anyio
async def test_a_valid_llm_choice_is_accepted_and_labelled():
    p = _StubProvider("GROCERY")
    intent = await FI.resolve_with_llm("forecast the big aisle", DEPARTMENTS, p)
    assert intent.department == "GROCERY"
    assert intent.resolved_by == "llm"


@pytest.mark.anyio
async def test_the_llm_is_not_called_when_rules_already_resolved():
    p = _StubProvider("PRODUCE")
    intent = await FI.resolve_with_llm(
        "What is next week's expected Grocery revenue?", DEPARTMENTS, p)
    assert intent.department == "GROCERY"
    assert intent.resolved_by == "deterministic"
    assert p.calls == 0, "a deterministic match must not cost a model call"


@pytest.fixture
def anyio_backend():
    return "asyncio"
