"""Definitions for the published aggregate tier.

Each entry is a name, a DDL statement and the SELECT that fills it from the
local star schema. Nothing here reads a fact table at query time on the hosted
side -- that is the whole point. The 3,713 MB local database becomes roughly
12 MB of computed results.

IDEMPOTENCE: every table is dropped and recreated inside one transaction per
table, so re-publishing is safe and a failed publish cannot leave a half-written
table visible. The alternative -- incremental upsert -- buys nothing here,
because the panel ended at day 711 and the source does not change.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PubTable:
    name: str
    ddl: str
    select: str
    note: str = ""


TABLES: list[PubTable] = [

    PubTable(
        "pub_weekly_revenue",
        """CREATE TABLE pub_weekly_revenue (
               week_no smallint PRIMARY KEY, start_date date, is_partial_week boolean,
               revenue numeric(14,2), baskets int, households int,
               cumulative_revenue numeric(16,2), rolling_7wk_avg numeric(14,2))""",
        """SELECT week_no, start_date, is_partial_week, revenue, baskets, households,
                  sum(revenue) OVER (ORDER BY week_no
                      ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW),
                  round(avg(revenue) OVER (ORDER BY week_no
                      ROWS BETWEEN 3 PRECEDING AND 3 FOLLOWING), 2)
           FROM (SELECT w.week_no, w.start_date, w.is_partial_week,
                        round(sum(ft.sales_value), 2) AS revenue,
                        count(DISTINCT ft.basket_id) AS baskets,
                        count(DISTINCT ft.household_key) AS households
                 FROM fact_transactions ft JOIN dim_week w ON w.week_no = ft.week_no
                 GROUP BY 1,2,3) x ORDER BY week_no""",
        "102 rows -- the executive revenue chart"),

    PubTable(
        "pub_weekly_revenue_by_dept",
        """CREATE TABLE pub_weekly_revenue_by_dept (
               week_no smallint, department text, revenue numeric(14,2),
               baskets int, households int,
               PRIMARY KEY (week_no, department))""",
        """SELECT ft.week_no, p.department, round(sum(ft.sales_value), 2),
                  count(DISTINCT ft.basket_id), count(DISTINCT ft.household_key)
           FROM fact_transactions ft JOIN dim_product p ON p.product_id = ft.product_id
           WHERE p.department IS NOT NULL
           GROUP BY 1,2""",
        "department drill-down without shipping the fact table"),

    PubTable(
        "pub_rfm_segments",
        """CREATE TABLE pub_rfm_segments (
               segment text PRIMARY KEY, households int, pct_of_panel numeric(5,1),
               avg_recency_days numeric(10,1), avg_baskets numeric(10,1),
               avg_lifetime_value numeric(12,2), segment_revenue numeric(14,2),
               pct_of_revenue numeric(5,1))""",
        """WITH bounds AS (SELECT max(day_number) AS last_day FROM fact_transactions),
                hm AS (SELECT household_key,
                              (SELECT last_day FROM bounds) - max(day_number) AS recency_days,
                              count(DISTINCT basket_id) AS frequency_baskets,
                              round(sum(sales_value), 2) AS monetary_value
                       FROM fact_transactions GROUP BY household_key),
                sc AS (SELECT *, ntile(5) OVER (ORDER BY recency_days DESC) r,
                                 ntile(5) OVER (ORDER BY frequency_baskets) f,
                                 ntile(5) OVER (ORDER BY monetary_value) m FROM hm),
                sg AS (SELECT *, CASE
                          WHEN r>=4 AND f>=4 AND m>=4 THEN 'Champions'
                          WHEN r>=3 AND f>=3 THEN 'Loyal'
                          WHEN r>=4 AND f<=2 THEN 'New / Promising'
                          WHEN r<=2 AND f>=4 AND m>=4 THEN 'At Risk - high value'
                          WHEN r<=2 AND f>=3 THEN 'At Risk'
                          WHEN r<=2 AND f<=2 THEN 'Lapsed'
                          ELSE 'Needs Attention' END AS segment FROM sc)
           SELECT segment, count(*), round(100.0*count(*)/sum(count(*)) OVER (), 1),
                  round(avg(recency_days), 1), round(avg(frequency_baskets), 1),
                  round(avg(monetary_value), 2), round(sum(monetary_value), 2),
                  round(100.0*sum(monetary_value)/sum(sum(monetary_value)) OVER (), 1)
           FROM sg GROUP BY segment""",
        "7 rows"),

    PubTable(
        "pub_retention_tenure",
        """CREATE TABLE pub_retention_tenure (
               segment text, tenure_month int, cohort_size int,
               active_households int, retention_pct numeric(5,1),
               PRIMARY KEY (segment, tenure_month))""",
        """WITH fp AS (SELECT household_key, min(date_key) fd, min(day_number) fday
                       FROM fact_transactions GROUP BY 1),
                fb AS (SELECT fp.household_key, fp.fday, sum(ft.sales_value) v
                       FROM fp JOIN fact_transactions ft
                         ON ft.household_key=fp.household_key AND ft.date_key=fp.fd
                       GROUP BY 1,2),
                co AS (SELECT household_key, fday, ntile(3) OVER (ORDER BY v) t FROM fb),
                ac AS (SELECT c.t, (ft.day_number-c.fday)/30 tm,
                               count(DISTINCT ft.household_key) ah
                       FROM fact_transactions ft JOIN co c USING (household_key)
                       WHERE (ft.day_number-c.fday)/30 <= 23 GROUP BY 1,2),
                cs AS (SELECT t, count(*) n FROM co GROUP BY 1)
           SELECT CASE a.t WHEN 1 THEN 'Smallest first basket'
                           WHEN 2 THEN 'Middle' ELSE 'Largest first basket' END,
                  a.tm, s.n, a.ah, round(100.0*a.ah/s.n, 1)
           FROM ac a JOIN cs s ON s.t=a.t""",
        "72 rows -- relative tenure, NOT calendar cohorts"),

    PubTable(
        "pub_pareto_products",
        """CREATE TABLE pub_pareto_products (
               revenue_rank int PRIMARY KEY, product_id int, commodity_desc text,
               department text, revenue numeric(12,2),
               cumulative_pct numeric(8,4))""",
        """WITH pr AS (SELECT ft.product_id, p.commodity_desc, p.department,
                              round(sum(ft.sales_value), 2) revenue
                       FROM fact_transactions ft
                       JOIN dim_product p ON p.product_id=ft.product_id
                       WHERE ft.sales_value > 0 GROUP BY 1,2,3),
                rk AS (SELECT *, row_number() OVER (ORDER BY revenue DESC) rnk,
                               sum(revenue) OVER (ORDER BY revenue DESC
                                   ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) cum,
                               sum(revenue) OVER () tot FROM pr)
           SELECT rnk, product_id, commodity_desc, department, revenue,
                  round(100.0*cum/tot, 4)
           FROM rk WHERE rnk <= 5000""",
        "top 5,000 products; the 80% line falls at rank 11,865 so the "
        "headline is stored separately in pub_headline"),

    PubTable(
        "pub_commodity_affinity",
        """CREATE TABLE pub_commodity_affinity (
               commodity_a text, commodity_b text, pair_baskets int,
               support numeric(10,6), lift numeric(10,3),
               PRIMARY KEY (commodity_a, commodity_b))""",
        """WITH bc AS (SELECT DISTINCT ft.basket_id, p.commodity_desc c
                       FROM fact_transactions ft
                       JOIN dim_product p ON p.product_id=ft.product_id
                       WHERE p.commodity_desc IS NOT NULL
                         AND ft.quantity BETWEEN 1 AND 1000),
                tot AS (SELECT count(DISTINCT basket_id) n FROM bc),
                it AS (SELECT c, count(*) b, count(*)::numeric/(SELECT n FROM tot) s
                       FROM bc GROUP BY c HAVING count(*) >= 500),
                pr AS (SELECT a.c ca, b.c cb, count(*) pb
                       FROM bc a JOIN bc b ON a.basket_id=b.basket_id AND a.c<b.c
                       JOIN it sa ON sa.c=a.c JOIN it sb ON sb.c=b.c
                       GROUP BY 1,2 HAVING count(*) >= 200)
           SELECT ca, cb, pb, round(pb::numeric/(SELECT n FROM tot), 6),
                  round((pb::numeric/(SELECT n FROM tot))/(sa.s*sb.s), 3)
           FROM pr JOIN it sa ON sa.c=ca JOIN it sb ON sb.c=cb
           WHERE (pb::numeric/(SELECT n FROM tot))/(sa.s*sb.s) > 1.0""",
        "market basket lift pairs"),

    PubTable(
        "pub_reorder_by_department",
        """CREATE TABLE pub_reorder_by_department (
               department text PRIMARY KEY, products int,
               avg_household_reorder_rate numeric(5,1),
               avg_line_reorder_rate numeric(5,1))""",
        """WITH hp AS (SELECT household_key, product_id,
                              count(DISTINCT date_key) occ
                       FROM fact_transactions
                       WHERE quantity BETWEEN 1 AND 1000 GROUP BY 1,2),
                ps AS (SELECT product_id, count(*) hh,
                              count(*) FILTER (WHERE occ>1) rh,
                              sum(occ) t, sum(occ-1) r
                       FROM hp GROUP BY 1 HAVING count(*) >= 50)
           SELECT p.department, count(*),
                  round(avg(100.0*ps.rh/ps.hh), 1),
                  round(avg(100.0*ps.r/ps.t), 1)
           FROM ps JOIN dim_product p ON p.product_id=ps.product_id
           WHERE p.department IS NOT NULL GROUP BY 1 HAVING count(*) >= 10""",
        "12 rows"),

    PubTable(
        "pub_promo_exposure",
        """CREATE TABLE pub_promo_exposure (
               week_no smallint, department text, promo_rows bigint,
               on_display bigint, in_mailer bigint, display_pct numeric(6,2),
               PRIMARY KEY (week_no, department))""",
        """SELECT fc.week_no, p.department, count(*),
                  count(*) FILTER (WHERE fc.display <> '0'),
                  count(*) FILTER (WHERE fc.mailer <> '0'),
                  round(100.0*count(*) FILTER (WHERE fc.display <> '0')
                        / nullif(count(*),0), 2)
           FROM fact_causal fc JOIN dim_product p ON p.product_id=fc.product_id
           WHERE p.department IS NOT NULL
           GROUP BY 1,2""",
        "collapses 36.8M causal rows to a few thousand"),

    PubTable(
        "pub_anomalies",
        """CREATE TABLE pub_anomalies (
               week_no smallint PRIMARY KEY, start_date date,
               revenue numeric(14,2), z_score numeric(8,3),
               mean_revenue numeric(14,2), is_partial_week boolean)""",
        """WITH w AS (SELECT wk.week_no, wk.start_date, wk.is_partial_week,
                             round(sum(ft.sales_value),2) revenue
                      FROM fact_transactions ft JOIN dim_week wk ON wk.week_no=ft.week_no
                      GROUP BY 1,2,3),
                s AS (SELECT avg(revenue) m, stddev_samp(revenue) sd
                      FROM w WHERE NOT is_partial_week)
           SELECT w.week_no, w.start_date, w.revenue,
                  round(((w.revenue-s.m)/nullif(s.sd,0))::numeric,3), round(s.m,2),
                  w.is_partial_week
           FROM w CROSS JOIN s
           WHERE abs((w.revenue-s.m)/nullif(s.sd,0)) >= 2.0""",
        "precomputed anomalies for narration"),

    PubTable(
        "pub_headline",
        """CREATE TABLE pub_headline (
               metric text PRIMARY KEY, value text, context text)""",
        """SELECT * FROM (VALUES
              ('products_for_80pct_revenue', NULL::text, NULL::text)) v(a,b,c)
           WHERE false""",
        "filled by the publisher from measured values, not recomputed here"),
]

# Dimensions published verbatim so hosted NL->SQL has a real schema to write
# against. These are small; the fact tables are what cannot travel.
DIMENSIONS = ["dim_date", "dim_week", "dim_store", "dim_household", "dim_campaign"]

# dim_product is 92,353 rows -- published with only the columns the dashboard
# and NL->SQL actually use, to keep the tier small.
DIM_PRODUCT_SELECT = """
    SELECT product_id, department, brand, commodity_desc, sub_commodity_desc
    FROM dim_product"""
