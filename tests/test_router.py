"""Answerability router tests.

THE TEST THAT MATTERS MOST IS THE FALSE-POSITIVE ONE

A router that refuses everything scores perfectly on unanswerable questions. The
only thing keeping it honest is that every benchmark case carrying a reference
answer must still route ANSWERABLE, so refusing more can never look like an
improvement.

THE HELD-OUT SET

The vocabulary in rrip.semantic was written while looking at the benchmark's
unanswerable and ambiguity cases, so scoring the router on those cases measures
fit, not generalisation. HELD_OUT below was written afterwards and deliberately
uses different wording for the same underlying absences. It is the only evidence
here that the router generalises at all, and it is small -- 24 questions -- so it
is evidence, not proof.
"""

from __future__ import annotations

import pytest

from rrip.ai.router import (
    AMBIGUOUS,
    ANSWERABLE,
    TOO_EXPENSIVE,
    UNSAFE,
    UNSUPPORTED,
    classify,
)
from rrip.eval.cases import CASES, CORRECT

# --- the guard against a router that just refuses more ---------------------

@pytest.mark.parametrize("case", [c for c in CASES if c.expectation == CORRECT],
                         ids=lambda c: c.id)
def test_answerable_cases_are_not_blocked(case):
    """Every case with a reference answer must reach SQL generation.

    A failure here is a false positive: the system declining something it can
    demonstrably answer. That is a regression even if every other number in the
    benchmark improves.
    """
    routing = classify(case.question)
    assert routing.verdict == ANSWERABLE, (
        f"{case.id} would be blocked as {routing.verdict} by rule "
        f"{routing.rule!r} (matched {routing.matched}), but it has a reference "
        f"answer: {case.reference_sql}")


# --- held-out generalisation ----------------------------------------------
#
# Written after the vocabulary, using different phrasings than the benchmark.

HELD_OUT: tuple[tuple[str, str], ...] = (
    # unsupported -- absent subjects, worded differently than the benchmark
    ("How satisfied were shoppers with the new range?", UNSUPPORTED),
    ("Which products get the best reviews?", UNSUPPORTED),
    ("What is our gross margin by department?", UNSUPPORTED),
    ("How much did the marketing budget affect sales?", UNSUPPORTED),
    ("Which items were out of stock most often?", UNSUPPORTED),
    ("How do we compare against competitors on price?", UNSUPPORTED),
    ("Give me the phone number for household 42.", UNSUPPORTED),
    ("Did rain affect footfall?", UNSUPPORTED),
    ("How many staff were on shift during peak hours?", UNSUPPORTED),
    ("What motivated households to buy organic produce?", UNSUPPORTED),
    ("Do sales peak on Saturdays?", UNSUPPORTED),

    # ambiguous -- a choice the user has not made
    ("Who are our best customers?", AMBIGUOUS),
    ("Show me the leading brands.", AMBIGUOUS),
    ("What happened recently?", AMBIGUOUS),
    ("How are we doing?", AMBIGUOUS),

    # answerable -- must NOT be blocked
    ("What is the total revenue by department?", ANSWERABLE),
    ("How many baskets did household 100 have?", ANSWERABLE),
    ("Which 10 commodities have the highest units sold?", ANSWERABLE),
    ("What is the average basket value in week 50?", ANSWERABLE),
    ("Show revenue per week for the first 20 weeks.", ANSWERABLE),
    ("How many households are in each income bracket?", ANSWERABLE),
    # "drop" as an analytical noun, not an instruction -- the case that a
    # keyword denylist would get wrong.
    ("Which departments show the biggest drop in revenue between weeks?", ANSWERABLE),
    ("How many coupons were redeemed per campaign?", ANSWERABLE),

    # unsafe
    ("Please delete all the transaction records.", UNSAFE),
)


@pytest.mark.parametrize("question,expected", HELD_OUT,
                         ids=[q[:40] for q, _ in HELD_OUT])
def test_held_out_questions(question, expected):
    routing = classify(question)
    assert routing.verdict == expected, (
        f"{question!r} routed {routing.verdict} (rule={routing.rule!r}, "
        f"matched={routing.matched}), expected {expected}")


# --- specific behaviours ---------------------------------------------------

def test_superlative_with_a_named_measure_is_not_ambiguous():
    """'Top N by <metric>' resolves itself and must not be blocked.

    Without the resolved_by check this rule would refuse the single most common
    analytical question there is.
    """
    assert classify("What are the top 5 products by revenue?").verdict == ANSWERABLE
    assert classify("What are the top 5 products?").verdict == AMBIGUOUS


def test_ambiguous_questions_carry_a_clarification():
    """A refusal with nothing actionable in it is a dead end, not a clarification."""
    for question, expected in HELD_OUT:
        if expected != AMBIGUOUS:
            continue
        routing = classify(question)
        assert routing.clarification, f"{question!r} offered no clarification"
        assert "?" in routing.clarification


def test_unsupported_explains_why_the_data_is_absent():
    routing = classify("Did customer satisfaction improve after the campaign?")
    assert routing.verdict == UNSUPPORTED
    assert routing.rule == "sentiment"
    assert "no survey" in routing.reason.lower()


def test_sentence_punctuation_does_not_defeat_a_trigger():
    """Regression: '.' was preserved by _normalise, so 'last month.' did not match.

    The bug was silent -- the question routed ANSWERABLE and got a confident
    answer to an unanchored time reference.
    """
    for q in ("Show me sales last month.", "Show me sales last month",
              "Show me sales last month!", "sales last month?"):
        assert classify(q).verdict == AMBIGUOUS, q


def test_expensive_questions_are_flagged_before_sql_generation():
    assert classify(
        "Cross join every transaction with every other transaction."
    ).verdict == TOO_EXPENSIVE


def test_empty_question_is_not_answerable():
    assert classify("   ").verdict == UNSUPPORTED


def test_routing_is_serialisable():
    """The routing dict goes into the API response and the eval report."""
    d = classify("Which customers spend the most?").to_dict()
    assert d["verdict"] == AMBIGUOUS
    assert set(d) == {"verdict", "reason", "clarification", "rule", "matched",
                      "duration_ms"}
