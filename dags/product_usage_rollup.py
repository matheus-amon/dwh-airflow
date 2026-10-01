"""Publish the feature adoption rollup once the warehouse has produced it.

This DAG does not schedule anything. It subscribes to an Airflow Asset, so it runs when — and only
when — ``mart_product_adoption`` has actually been rebuilt, and it reads the mart through the same
Airflow Connection the dbt task used to write it.

The task is a real one: it reads the mart, compares the latest month against the previous one, and
writes a dated CSV. A downstream DAG whose only action is printing its own asset name is a
placeholder that looks like an integration.
"""

from __future__ import annotations

import csv
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import pendulum
import psycopg
from airflow.sdk import Asset, BaseHook, dag, get_current_context, task

from dwh_pipelines.warehouse import asset_uri, mart_relation, resolve_environment

log = logging.getLogger(__name__)

ENVIRONMENT = resolve_environment()

ADOPTION_MART_NAME = "mart_product_adoption"
ADOPTION_MART_RELATION = mart_relation(ENVIRONMENT, ADOPTION_MART_NAME)

#: Declared as a dataset rather than a connection string. When the daily build finishes, this
#: asset is what makes this DAG eligible to run.
ADOPTION_MART = Asset(
    name=ADOPTION_MART_NAME,
    uri=asset_uri(ENVIRONMENT, ADOPTION_MART_RELATION),
    group="warehouse",
)

OUTPUT_DIR = Path(os.getenv("DWH_EXPORT_DIR", "/opt/airflow/exports"))

#: Feature rows to include in the rollup. Everything in the mart is exported; this is only the
#: ordering, which puts the paid features first.
FEATURE_ORDER = (
    "advanced_analytics",
    "sso",
    "audit_log",
    "api_access",
    "data_export",
    "core_dashboard",
    "core",
)


@dag(
    dag_id="product_usage_rollup",
    description=(
        "Reads mart_product_adoption and writes a week-over-week adoption report. "
        "Asset-driven: runs when the mart is rebuilt, not on a schedule. "
        f"Targets {ENVIRONMENT.name} ({ENVIRONMENT.database})."
    ),
    schedule=[ADOPTION_MART],
    start_date=pendulum.datetime(2025, 6, 1, tz="UTC"),
    catchup=False,
    tags=["warehouse", "reporting", ENVIRONMENT.name],
    default_args={"retries": 2, "retry_delay": pendulum.duration(minutes=2)},
)
def product_usage_rollup():

    @task
    def export_adoption() -> str:
        """Write the latest adoption month, with the change against the month before."""

        # The Connection is resolved here, at run time, not when the DAG file is parsed. Reading
        # it during the import makes the DAG unloadable on a fresh deployment where the
        # connection has not been created yet — the scheduler reports an import error and never
        # shows the DAG at all.
        conn = BaseHook.get_connection(ENVIRONMENT.connection_id)

        query = """
            with latest as (
                -- The latest *complete* month. Taking max(month_start_date) picks the month in
                -- progress, which on the first of the month contains a single day of telemetry
                -- and reports every feature as collapsing by most of its adoption rate. A number
                -- that wrong is worse than no number.
                select max(month_start_date) as month_start_date
                from {relation}
                where month_start_date < date_trunc('month', current_date)
            )
            select
                current.month_start_date,
                current.feature,
                current.plan_code,
                current.entitled_accounts,
                current.accounts_using_feature,
                round(current.entitled_adoption_rate::numeric, 4) as entitled_adoption_rate,
                round(previous.entitled_adoption_rate::numeric, 4) as prior_adoption_rate
            from {relation} as current
            cross join latest
            left join {relation} as previous
                on  previous.month_start_date = latest.month_start_date - interval '1 month'
                and previous.feature = current.feature
                and previous.plan_code = current.plan_code
            where current.month_start_date = latest.month_start_date
            order by current.feature, current.plan_code
        """.format(relation=ADOPTION_MART_RELATION)

        with psycopg.connect(
            host=conn.host,
            port=conn.port,
            user=conn.login,
            password=conn.password,
            dbname=ENVIRONMENT.database,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(query)
                columns = [c.name for c in cursor.description]
                rows = cursor.fetchall()

        if not rows:
            raise ValueError(
                f"{ADOPTION_MART_RELATION} returned no rows for its latest month"
            )

        order = {feature: index for index, feature in enumerate(FEATURE_ORDER)}
        rows.sort(key=lambda row: (order.get(row[1], len(order)), row[2]))

        context = get_current_context()
        month = rows[0][0]
        output = OUTPUT_DIR / f"feature_adoption_{month.isoformat()}.csv"
        output.parent.mkdir(parents=True, exist_ok=True)

        with output.open("w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(columns + ["adoption_change_pp"])
            for row in rows:
                rate, prior = row[5], row[6]
                change = None
                if rate is not None and prior is not None:
                    # Percentage points, not a ratio: reporting a ratio of rates invites reading
                    # a 10-point drop as a 90% drop.
                    change = round((float(rate) - float(prior)) * 100, 2)
                writer.writerow(list(row) + [change])

        log.info(
            "wrote %s rows for %s to %s (logical date %s)",
            len(rows),
            month,
            output,
            context.get("logical_date", datetime.now(timezone.utc)),
        )
        return str(output)

    export_adoption()


product_usage_rollup()
