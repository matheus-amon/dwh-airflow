"""Build the dbt warehouse once a day.

The whole task graph comes from the dbt project: one task per model and per test, with the
dependencies dbt already knows about. Nothing about the warehouse's shape is duplicated here, so
adding a model to dwh-dbt adds it to this DAG with no change here at all.

DbtDag is itself a DAG, not an operator dropped into one — it is constructed at module level and
given its own dag_id, schedule and tags, rather than being wrapped in a @dag decorator.
"""

from __future__ import annotations

import pendulum

from cosmos import DbtDag, RenderConfig

from dwh_pipelines.warehouse import (
    execution_config,
    profile_config,
    project_config,
    resolve_environment,
)

ENVIRONMENT = resolve_environment()

warehouse_build = DbtDag(
    dag_id="dbt_daily_build",
    description=(
        f"Builds the warehouse in ../dwh-dbt against {ENVIRONMENT.name} "
        f"({ENVIRONMENT.database})."
    ),
    schedule="@daily",
    # Pinned rather than relative, so the schedule does not silently move when Airflow restarts.
    start_date=pendulum.datetime(2025, 6, 1, tz="UTC"),
    catchup=False,
    # One at a time: two concurrent builds against one warehouse is a race on the same tables.
    max_active_runs=1,
    tags=["dbt", "warehouse", ENVIRONMENT.name],
    default_args={"retries": 2, "retry_delay": pendulum.duration(minutes=5)},

    project_config=project_config(),
    # The target comes from the profile, not from a node selection: target_name is the resolved
    # environment, so `dbt build` runs against dev or prod according to where this Airflow is
    # deployed. Repointing at production changes one variable, not this file.
    profile_config=profile_config(ENVIRONMENT),
    execution_config=execution_config(),
    render_config=RenderConfig(
        # Resolves packages.yml before the run, so adding a dbt dependency does not need an
        # image rebuild.
        dbt_deps=True,
        # test_behavior defaults to after-each, which is what makes a failed data test fail this
        # DAG run rather than reporting success and moving on. Stated here rather than left
        # implicit, because it is the difference between a green DAG and a correct one.
    ),
)
