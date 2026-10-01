"""DAG integrity tests.

The tests here exist because a broken DAG is invisible in the Airflow UI until a schedule misses:
the file parses, no error is shown, and nothing runs. These run in CI against the real image, so
a broken DAG fails the build rather than a schedule.
"""

from __future__ import annotations

import os
import pathlib

import pytest

DAGS_FOLDER = pathlib.Path(
    os.getenv("AIRFLOW__CORE__DAGS_FOLDER", pathlib.Path(__file__).resolve().parents[1] / "dags")
)

#: Every DAG must carry at least one of these, so the UI can group them and a new pipeline cannot
#: silently join the unfiltered list. Widened deliberately: the point is that an untagged DAG is a
#: conversation someone has to have twice.
APPROVED_TAGS = {
    "dbt",
    "warehouse",
    "reporting",
    "dev",
    "prod",
}

#: Minimum retry count. Every DAG here hits a warehouse over a network; a single attempt makes a
#: transient connection reset look like a pipeline failure.
MIN_RETRIES = 2


@pytest.fixture(scope="session")
def dagbag():
    from airflow.models.dagbag import DagBag

    # No include_examples: Airflow 3 dropped it from DagBag's signature, so passing it is a
    # TypeError rather than a default.
    return DagBag(dag_folder=str(DAGS_FOLDER))


@pytest.fixture(scope="session")
def dags(dagbag):
    assert not dagbag.import_errors, (
        "DAG files failed to import:\n"
        + "\n".join(f"  {path}: {error}" for path, error in dagbag.import_errors.items())
    )
    return dagbag.dags


def test_the_expected_dags_are_discovered(dags):
    assert set(dags) == {"dbt_daily_build", "product_usage_rollup"}


def test_every_dag_is_scheduled(dags):
    unscheduled = [dag_id for dag_id, dag in dags.items() if dag.schedule is None]
    assert not unscheduled, f"unscheduled DAGs: {unscheduled}"


def test_every_dag_has_tags_within_the_approved_set(dags):
    for dag_id, dag in dags.items():
        assert dag.tags, f"{dag_id} has no tags"
        assert set(dag.tags) <= APPROVED_TAGS, (
            f"{dag_id} has unapproved tags: {sorted(set(dag.tags) - APPROVED_TAGS)}"
        )


def test_every_dag_retries_enough(dags):
    for dag_id, dag in dags.items():
        assert dag.default_args.get("retries", 0) >= MIN_RETRIES, f"{dag_id} retries too rarely"


def test_every_dag_file_declares_exactly_one_dag(dags):
    """DagBag keys by dag_id, so two DAGs sharing one silently means only the last is reachable.

    Counting files against discovered DAGs catches that: a second DAG sharing an id would show up
    as fewer DAGs than files.
    """
    dag_files = [
        path for path in sorted(DAGS_FOLDER.glob("*.py")) if not path.name.startswith("_")
    ]
    assert dag_files, "no DAG files found"
    assert len(dag_files) == len(dags), (
        f"{len(dag_files)} DAG files produced {len(dags)} DAGs — a dag_id is probably shared"
    )


def test_every_dag_carries_its_environment(dags):
    """A DAG must say which warehouse it targets, in its tags and in its description."""
    environment = os.getenv("DWH_ENVIRONMENT", "dev").strip().lower()
    for dag_id, dag in dags.items():
        assert environment in dag.tags, f"{dag_id} is not tagged with its environment"
        assert environment in (dag.description or ""), (
            f"{dag_id} does not state its target environment in the description"
        )


def test_the_daily_build_runs_the_whole_project(dags):
    dag = dags["dbt_daily_build"]
    # Cosmos expands the dbt graph into tasks, so a non-trivial task count is evidence the
    # project was found and rendered rather than silently resolving to nothing.
    assert len(dag.tasks) > 10, f"only {len(dag.tasks)} tasks: did the dbt project resolve?"


def test_the_rollup_is_asset_driven(dags):
    dag = dags["product_usage_rollup"]
    assert [task.task_id for task in dag.tasks] == ["export_adoption"]
    # It must consume the mart rather than run on a cron. On a schedule it could read a mart that
    # has not been rebuilt, and the report would be stale with nothing signalling it.
    assert not isinstance(dag.schedule, str), "the rollup should be asset-driven, not cron-driven"


def test_the_daily_build_is_cron_driven(dags):
    assert dags["dbt_daily_build"].schedule == "@daily"
