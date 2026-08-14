"""Predictive layer: one-week-ahead department revenue forecasting.

Completes the descriptive / predictive / causal triad. The rule the rest of the
project is built around holds here without modification: the LLM does not
compute the forecast. It may route a question to this module and translate the
request into structured parameters, and it may narrate fields this module
produced -- the number itself comes from a fitted model with a recorded seed,
scored on a temporal test set that was locked during selection.

Read rrip.forecast.contract first. It is the specification, and everything else
derives its windows, its features and its cutoff rule from it.
"""

from rrip.forecast.contract import (
    CONTRACT,
    CONTRACT_VERSION,
    FEATURE_NAMES,
    FEATURE_VERSION,
    HORIZON_WEEKS,
    SUPPORTED_HORIZONS,
    TARGET_NAME,
)

__all__ = [
    "CONTRACT",
    "CONTRACT_VERSION",
    "FEATURE_NAMES",
    "FEATURE_VERSION",
    "HORIZON_WEEKS",
    "SUPPORTED_HORIZONS",
    "TARGET_NAME",
]
