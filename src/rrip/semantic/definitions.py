"""The business semantic layer: one definition of each metric, in one place.

WHY THIS EXISTS

The metric semantics were previously prose inside SCHEMA_PROMPT -- "sales_value
is the NET amount charged", "quantity > 1000 means weighted goods in grams".
That is a real semantic layer with no enforcement: nothing checked that the
generated SQL honoured it, nothing could evaluate against it, and the same rule
had to be restated in the published-tier prompt, in the docs, and in every
reference query in the benchmark. Four copies drift.

So the definitions live here and everything else is generated from them:

  * rrip.ai.nl2sql       builds its prompt fragment from render_prompt()
  * rrip.ai.router       derives its vocabulary from KNOWN_TERMS / ABSENT_DOMAINS
  * rrip.eval            grades metric-semantic cases against Metric.expression
  * docs                 rrip semantic-export writes the table

WHY PYTHON AND NOT YAML

A YAML file needs a parser, and PyYAML is not in the core dependency set. That
set is deliberately 30.0 MB because Vercel's serverless limit is 250 MB and the
scientific stack is 332.9 MB (see pyproject.toml). Adding a dependency to the
serverless path to express a static dict would be a real cost for no gain. These
dataclasses are machine-readable, importable without a parse step, and
`rrip semantic-export` renders them to YAML and Markdown for anyone who wants
the flat file.

WHAT AN ENTRY IS NOT

`expression` is the definition of the metric, not a promise that generated SQL
will be textually identical to it. Two spellings of the same aggregate are the
same metric. It is used to build reference results and to state the rule in the
prompt; correctness is still judged by executing and comparing results.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Metric:
    """A business quantity with exactly one agreed computation."""

    name: str
    expression: str
    grain: str
    description: str
    # Alternative phrasings a user might type. These feed the router's
    # vocabulary, so an unlisted synonym makes a question look unrecognised
    # rather than producing a wrong answer silently.
    synonyms: tuple[str, ...] = ()
    # The published tier aggregates ahead of time, so the same metric has a
    # different expression there. None means the metric is unavailable there.
    published_expression: str | None = None
    caveats: tuple[str, ...] = ()


@dataclass(frozen=True)
class Dimension:
    """Something a metric can be grouped or filtered by."""

    name: str
    table: str
    column: str
    description: str
    synonyms: tuple[str, ...] = ()


@dataclass(frozen=True)
class AbsentDomain:
    """A subject this dataset holds no data for, and why.

    This is the honest core of answerability. The schema cannot answer questions
    about sentiment not because sentiment is a strange thing to ask, but because
    dunnhumby's Complete Journey contains transactions, promotions and coarse
    demographics -- and nothing else. Naming the absence explicitly turns a
    fabricated answer into a stated limitation.

    `triggers` are the words that indicate the domain. They are matched as whole
    words against the question. Being wrong in the safe direction here costs a
    clarification prompt; being wrong in the other direction costs a fabricated
    number, so the list errs toward catching.
    """

    name: str
    reason: str
    triggers: tuple[str, ...]


@dataclass(frozen=True)
class AmbiguityRule:
    """A question shape with more than one defensible reading.

    `needs` names what the user must supply. The clarification text is shown to
    the user, so it is written as a question, not as an error.
    """

    name: str
    reason: str
    clarification: str
    # Whole-word triggers. A rule fires only when a trigger matches AND none of
    # `resolved_by` appears -- "top products by revenue" names its measure and
    # is therefore not ambiguous.
    triggers: tuple[str, ...]
    resolved_by: tuple[str, ...] = ()


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------
#
# Every caveat below is a real defect this dataset produced, not a hypothetical.
# See docs/methodology-notes.md.

METRICS: tuple[Metric, ...] = (
    Metric(
        name="revenue",
        expression="sum(sales_value)",
        grain="transaction line",
        description="Net amount charged to the customer, after retail discount.",
        synonyms=("revenue", "sales", "turnover", "net sales", "spend", "spending",
                  "sales value", "money", "income"),
        published_expression="sum(revenue)",
        caveats=("sales_value is NET. It is not gross_value.",),
    ),
    Metric(
        name="gross_value",
        expression="sum(gross_value)",
        grain="transaction line",
        description=("Value before retail discount. gross_value = sales_value - "
                     "retail_disc, and retail_disc is stored NEGATIVE, so gross "
                     "is the larger number."),
        synonyms=("gross value", "gross revenue", "gross sales", "before discount",
                  "pre-discount", "list price value"),
        published_expression=None,
        caveats=("retail_disc is stored NEGATIVE; subtracting it adds value.",
                 "Not available on the published tier."),
    ),
    Metric(
        name="basket_value",
        expression="sum(sales_value) / count(DISTINCT basket_id)",
        grain="basket",
        description="Average value of a shopping basket (a single store visit).",
        synonyms=("basket value", "average basket", "basket size", "average order",
                  "order value", "transaction value", "spend per visit",
                  "average spend per trip"),
        published_expression="sum(revenue) / sum(baskets)",
        caveats=("Per basket_id, NOT per transaction line. A basket holds many "
                 "lines, so dividing by count(*) understates it several-fold.",),
    ),
    Metric(
        name="units_sold",
        expression="sum(quantity) FILTER (WHERE quantity BETWEEN 1 AND 1000)",
        grain="transaction line",
        description="Count of items sold, excluding weighted goods.",
        synonyms=("units", "units sold", "quantity", "items sold", "volume",
                  "how many items", "pieces"),
        published_expression=None,
        caveats=("quantity > 1000 is weighted produce recorded in GRAMS, not a "
                 "count. Including it turns a banana into 1,360 units.",
                 "quantity <= 0 is a return."),
    ),
    Metric(
        name="baskets",
        expression="count(DISTINCT basket_id)",
        grain="basket",
        description="Number of distinct store visits.",
        synonyms=("baskets", "visits", "trips", "shopping trips", "orders",
                  "transactions per household"),
        published_expression="sum(baskets)",
    ),
    Metric(
        name="transaction_lines",
        expression="count(*)",
        grain="transaction line",
        description="Number of product lines scanned. NOT the number of visits.",
        synonyms=("transaction lines", "line items", "rows", "scans", "records"),
        published_expression=None,
        caveats=("A 'transaction' in business language usually means a basket. "
                 "This dataset's fact table is one row per product per basket.",),
    ),
    Metric(
        name="active_households",
        expression="count(DISTINCT household_key)",
        grain="household",
        description="Households with at least one transaction in the window.",
        synonyms=("households", "customers", "shoppers", "active households",
                  "customer count", "panel size"),
        published_expression="sum(households)",
        caveats=("The panel is 2,500 households. Only 801 have demographics, and "
                 "those 801 generate ~55% of transactions, so any demographic "
                 "breakdown is a biased subsample.",),
    ),
    Metric(
        name="coupon_discount",
        expression="sum(coupon_disc)",
        grain="transaction line",
        description="Value of manufacturer coupon discount applied.",
        synonyms=("coupon discount", "coupon value", "coupon savings"),
        published_expression=None,
        caveats=("Stored NEGATIVE, like retail_disc.",),
    ),
)


# --------------------------------------------------------------------------
# Dimensions
# --------------------------------------------------------------------------

DIMENSIONS: tuple[Dimension, ...] = (
    Dimension("department", "dim_product", "department",
              "Broad product grouping, e.g. GROCERY.",
              ("department", "departments", "category", "categories", "aisle")),
    Dimension("commodity", "dim_product", "commodity_desc",
              "Finer product grouping than department.",
              ("commodity", "commodities", "product type", "product group")),
    Dimension("brand", "dim_product", "brand",
              "National or Private label.",
              ("brand", "brands", "private label", "national brand")),
    Dimension("product", "dim_product", "product_id",
              "An individual product.",
              ("product", "products", "item", "items", "sku", "skus")),
    Dimension("household", "dim_household", "household_key",
              "A panel household. The customer grain of this dataset.",
              ("household", "households", "customer", "customers", "shopper",
               "shoppers", "member", "panelist")),
    Dimension("store", "dim_store", "store_id",
              "A retail location.", ("store", "stores", "shop", "location")),
    Dimension("campaign", "dim_campaign", "campaign_id",
              "A marketing campaign households were enrolled in.",
              ("campaign", "campaigns", "promotion", "promotions", "marketing")),
    Dimension("coupon", "dim_coupon", "coupon_upc",
              "A coupon, linked to products and campaigns.",
              ("coupon", "coupons", "voucher", "vouchers")),
    Dimension("week", "dim_week", "week_no",
              "Week 1-102. Weeks 1 and 102 are partial.",
              ("week", "weeks", "weekly")),
    Dimension("date", "dim_date", "date_key",
              "A calendar day, derived from DAY 1..711.",
              ("day", "days", "date", "dates", "daily", "month", "months",
               "monthly", "quarter", "year", "period", "time")),
    Dimension("income", "dim_household", "income_desc",
              "Household income band. Only 801 households have it.",
              ("income", "income bracket", "income band", "earnings")),
    Dimension("age", "dim_household", "age_desc",
              "Household age band. Only 801 households have it.",
              ("age", "age group", "age band", "demographic", "demographics")),
)


# --------------------------------------------------------------------------
# Absent domains -- what this dataset simply does not contain
# --------------------------------------------------------------------------
#
# Derived from docs/dataset-profile.md. dunnhumby's Complete Journey is
# transactions, promotions, coupons and coarse demographics. Everything below is
# a subject a business user reasonably asks about and this schema cannot touch.

ABSENT_DOMAINS: tuple[AbsentDomain, ...] = (
    AbsentDomain(
        name="sentiment",
        reason=("No survey, review, rating or complaint data exists. The dataset "
                "records what was bought, never what anyone thought about it."),
        triggers=("sentiment", "satisfaction", "satisfied", "happy", "unhappy",
                  "opinion", "opinions", "review", "reviews", "rating", "ratings",
                  "nps", "complaint", "complaints", "feedback", "loyalty score",
                  "brand perception", "attitude"),
    ),
    AbsentDomain(
        name="motivation",
        reason=("No stated-reason data exists. Purchases are observed; the reason "
                "for them is not recorded and cannot be inferred from this schema."),
        triggers=("why did", "why do", "why does", "reason customers", "motivation",
                  "because they", "what made them", "what caused customers",
                  "switch brands", "switched brands", "brand switching"),
    ),
    AbsentDomain(
        name="pii",
        reason=("This dataset is anonymised. Households are integer keys. No name, "
                "email, address, phone number or any other identifier exists."),
        triggers=("email", "emails", "e-mail", "name", "names", "address",
                  "addresses", "phone", "telephone", "postcode", "zip code",
                  "contact details", "date of birth", "personal details"),
    ),
    AbsentDomain(
        name="cost_and_margin",
        reason=("The dataset records revenue and discounts only. There is no cost "
                "of goods, no advertising spend and no supplier price, so margin "
                "and ROI cannot be computed -- only revenue-side effects."),
        triggers=("profit", "profits", "profitability", "margin", "margins",
                  "cost of goods", "cogs", "advertising", "advertise", "ad spend",
                  "marketing spend", "marketing budget", "budget", "roi",
                  "return on investment", "wholesale", "supplier cost", "overhead"),
    ),
    AbsentDomain(
        name="inventory",
        reason=("No stock, warehouse or supply-chain data exists. Out-of-stock "
                "events are invisible: an item nobody bought looks identical to "
                "an item nobody could buy."),
        triggers=("inventory", "stock level", "stock levels", "out of stock",
                  "stockout", "stockouts", "warehouse", "supply chain", "shelf "
                  "space", "restock", "shrinkage"),
    ),
    AbsentDomain(
        name="competitor",
        reason=("The panel observes one retailer. Purchases made elsewhere are "
                "not recorded, so share of wallet and competitive comparison are "
                "outside the data."),
        triggers=("competitor", "competitors", "competition", "market share",
                  "share of wallet", "rival", "versus walmart", "other retailers"),
    ),
    AbsentDomain(
        name="staffing_and_ops",
        reason=("No employee, till, queue or operational data exists. Store "
                "records carry an id and promotion coverage, nothing more."),
        triggers=("employee", "employees", "staff", "staffing", "cashier",
                  "queue", "waiting time", "checkout time", "labour", "labor"),
    ),
    AbsentDomain(
        name="weekday",
        reason=("Weekday labels are a modelling convention, not source data. "
                "dunnhumby publishes DAY 1..711 with no start date; the calendar "
                "anchor was chosen so derived weeks align with the source WEEK_NO, "
                "which fixes the day-of-week mapping arbitrarily. A 'Tuesday' in "
                "this schema is an artefact of that choice."),
        triggers=("day of the week", "day of week", "weekday", "weekdays",
                  "weekend", "weekends", "monday", "tuesday", "wednesday",
                  "thursday", "friday", "saturday", "sunday"),
    ),
    AbsentDomain(
        name="external_context",
        reason=("No weather, holiday calendar, macroeconomic or event data is "
                "joined to this schema. The calendar is derived from DAY 1..711 "
                "with an arbitrary anchor year, so real-world dates are not "
                "meaningful."),
        triggers=("weather", "rain", "temperature", "holiday", "christmas",
                  "thanksgiving", "easter", "inflation", "recession", "pandemic",
                  "covid"),
    ),
)


# --------------------------------------------------------------------------
# Ambiguity rules
# --------------------------------------------------------------------------
#
# These do not describe missing data. They describe questions whose answer
# depends on a choice the user has not made. Answering anyway is not wrong so
# much as arbitrary -- and an arbitrary answer presented as a fact is the
# failure mode this whole project exists to avoid.

AMBIGUITY_RULES: tuple[AmbiguityRule, ...] = (
    AmbiguityRule(
        name="unanchored_relative_time",
        reason=("This dataset has no real calendar. It spans DAY 1..711 with an "
                "arbitrary anchor year, so 'last month' has no defined meaning -- "
                "relative to today, to the dataset's final day, or to a campaign?"),
        clarification=("Which period do you mean? You can name a week (1-102), a "
                       "day range (1-711), or say 'the final month of the dataset'."),
        triggers=("last month", "this month", "last year", "this year", "last week",
                  "this week", "recently", "lately", "currently", "right now",
                  "year to date", "ytd", "last quarter", "past month"),
    ),
    AmbiguityRule(
        name="unspecified_measure",
        reason=("A superlative without a measure. 'Top products' can mean by "
                "revenue, by units, by basket penetration or by household reach, "
                "and these produce different lists."),
        clarification=("Ranked by what? Revenue, units sold, number of baskets, or "
                       "number of households?"),
        triggers=("top", "best", "worst", "biggest", "largest", "smallest",
                  "highest", "lowest", "most popular", "leading", "strongest"),
        resolved_by=("revenue", "sales", "spend", "units", "quantity", "baskets",
                     "visits", "households", "customers", "value", "profit",
                     "count", "volume", "frequency", "penetration", "lift"),
    ),
    AmbiguityRule(
        name="unspecified_customer_measure",
        reason=("'Spends the most' can mean lifetime spend, average basket, or "
                "spend in a recent window, and they rank households differently."),
        clarification=("Over what window and measure? Lifetime spend, average "
                       "basket value, or spend within a specific week range?"),
        triggers=("spend the most", "spends the most", "biggest spenders",
                  "top customers", "best customers", "most valuable customers",
                  "most loyal", "loyalty"),
        resolved_by=("lifetime", "total", "average basket", "per basket",
                     "week", "weeks", "day", "days", "rfm", "segment"),
    ),
    AmbiguityRule(
        name="no_metric_named",
        reason=("The question names no measurable quantity at all, so any answer "
                "would be the system choosing the subject on the user's behalf."),
        clarification=("Which measure are you interested in? For example revenue, "
                       "baskets, active households, or average basket value."),
        triggers=("how is the business doing", "how are we doing", "how's business",
                  "give me an overview", "tell me about the data",
                  "what's interesting", "any insights", "summarise everything",
                  "how did we perform", "overall performance"),
    ),
)


# --------------------------------------------------------------------------
# Predictive questions
# --------------------------------------------------------------------------
#
# A forecast question is not a SQL question. "What is next week's expected
# Grocery revenue?" has no answer in the fact table, because the answer has not
# happened yet -- and the failure mode if it is treated as a SQL question is
# the worst kind available here: the model writes something plausible over
# historical rows and returns a number that looks like a forecast.
#
# So predictive intent is detected in the same deterministic pass that detects
# absent subjects, and dispatched to rrip.forecast, which computes it from a
# fitted predictor that was scored on a temporal test set.

FORECAST_TRIGGERS: tuple[str, ...] = (
    "forecast", "forecasts", "forecasting", "predict", "prediction",
    "predicted", "projected", "projection", "next week", "next month",
    "coming week", "upcoming week", "week ahead", "weeks ahead",
    "expected revenue", "expect to", "how much will", "what will",
    "going to be", "outlook", "estimate for next", "anticipate",
)

# The forecaster has exactly one target. A predictive question about anything
# else is refused rather than answered with the nearest available series --
# naming the limit is the whole point of declaring it here.
FORECASTABLE_METRICS: tuple[str, ...] = ("revenue",)

FORECAST_SCOPE_REASON = (
    "The forecasting model predicts weekly revenue by department, one week "
    "ahead, and nothing else. Units, baskets, household counts and "
    "household-level spend were not modelled, so there is no measured error "
    "for them and a number would be an extrapolation with no evidence behind "
    "it.")


def forecastable_metric_terms() -> frozenset[str]:
    """Synonyms that indicate a predictive question is about revenue."""
    terms: set[str] = set()
    for m in METRICS:
        if m.name in FORECASTABLE_METRICS:
            terms.add(m.name.replace("_", " "))
            terms.update(m.synonyms)
    return frozenset(t.lower() for t in terms)


def unforecastable_metric_terms() -> frozenset[str]:
    """Synonyms for metrics the forecaster does NOT cover."""
    terms: set[str] = set()
    for m in METRICS:
        if m.name in FORECASTABLE_METRICS:
            continue
        terms.add(m.name.replace("_", " "))
        terms.update(m.synonyms)
    return frozenset(t.lower() for t in terms) - forecastable_metric_terms()


# --------------------------------------------------------------------------
# Derived vocabulary
# --------------------------------------------------------------------------


def known_terms() -> frozenset[str]:
    """Every word the schema has a concept for.

    Used by the router to decide whether a question is *about* anything this
    dataset holds. Built from the definitions above rather than hand-listed, so
    adding a metric extends the vocabulary automatically.
    """
    terms: set[str] = set()
    for m in METRICS:
        terms.add(m.name.replace("_", " "))
        terms.update(m.synonyms)
    for d in DIMENSIONS:
        terms.add(d.name)
        terms.update(d.synonyms)
    return frozenset(t.lower() for t in terms)


def metric_by_name(name: str) -> Metric | None:
    for m in METRICS:
        if m.name == name:
            return m
    return None


def render_prompt(published: bool = False) -> str:
    """The metric-definition block injected into the NL->SQL system prompt.

    Generated rather than written out, so a change to a definition reaches the
    model, the router, the evaluator and the docs in one edit.
    """
    lines = ["METRIC DEFINITIONS -- use these exact computations:"]
    for m in METRICS:
        expr = m.published_expression if published else m.expression
        if expr is None:
            continue
        lines.append(f"  {m.name} = {expr}   [per {m.grain}]")
        lines.append(f"      {m.description}")
        for c in m.caveats:
            lines.append(f"      ! {c}")
    if not published:
        lines.append("")
        lines.append("SUBJECTS THIS SCHEMA CANNOT ANSWER -- say so rather than "
                     "approximating:")
        for a in ABSENT_DOMAINS:
            lines.append(f"  {a.name}: {a.reason}")
    return "\n".join(lines)
