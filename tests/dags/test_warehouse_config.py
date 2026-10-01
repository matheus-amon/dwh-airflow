"""Unit tests for the environment resolution.

Pure functions, so these run without Airflow and without a warehouse. The behaviour that matters
is the failure path: a typo in the environment must not silently point a deployment at development.
"""

from __future__ import annotations

import pytest

from pathlib import Path

from dwh_pipelines import warehouse


def test_absent_environment_falls_back_to_dev(monkeypatch):
    monkeypatch.delenv("DWH_ENVIRONMENT", raising=False)
    assert warehouse.resolve_environment().name == "dev"


def test_a_known_environment_resolves(monkeypatch):
    monkeypatch.setenv("DWH_ENVIRONMENT", "prod")
    resolved = warehouse.resolve_environment()
    assert resolved.name == "prod"
    # prod must not silently fall back to a development host.
    assert resolved.host == warehouse.ENVIRONMENTS["prod"].host


def test_the_value_is_case_and_space_insensitive(monkeypatch):
    monkeypatch.setenv("DWH_ENVIRONMENT", "  PROD  ")
    assert warehouse.resolve_environment().name == "prod"


def test_an_unknown_environment_raises(monkeypatch):
    monkeypatch.setenv("DWH_ENVIRONMENT", "prodction")
    with pytest.raises(ValueError, match="prodction"):
        warehouse.resolve_environment()


def test_an_explicit_argument_overrides_the_variable(monkeypatch):
    monkeypatch.setenv("DWH_ENVIRONMENT", "dev")
    assert warehouse.resolve_environment("prod").name == "prod"


def test_environments_point_at_different_connections():
    ids = {env.connection_id for env in warehouse.ENVIRONMENTS.values()}
    assert len(ids) == len(warehouse.ENVIRONMENTS), "two environments share one Connection"


def test_asset_uri_identifies_the_dataset_without_the_host():
    """A URI carrying the production host ends up in the Airflow UI and in task logs."""
    relation = warehouse.mart_relation(warehouse.ENVIRONMENTS["dev"], "mart_product_adoption")

    for name, environment in warehouse.ENVIRONMENTS.items():
        uri = warehouse.asset_uri(environment, relation)
        assert uri == f"warehouse://{environment.database}/marts.mart_product_adoption", name
        # The host is what must not leak — the database and the relation are what identify it.
        if environment.host:
            assert environment.host not in uri, name
        assert f":{environment.port}" not in uri, name


def test_mart_relation_is_schema_qualified_but_not_database_qualified():
    """`public.marts.x` makes Postgres read `public` as a database and refuse the reference."""
    relation = warehouse.mart_relation(warehouse.ENVIRONMENTS["dev"], "mart_product_adoption")
    assert relation == "marts.mart_product_adoption"
    assert relation.count(".") == 1
    # The Postgres schema from the profile is not the mart schema, and mixing them up is the
    # whole bug this helper exists to prevent.
    assert warehouse.ENVIRONMENTS["dev"].schema != warehouse.MART_SCHEMA


def test_the_dbt_project_name_matches_the_dbt_project():
    """A mismatch here makes Cosmos build a different project than the one that exists."""
    project_yml = Path(warehouse.DBT_PROJECT_PATH) / "dbt_project.yml"
    if not project_yml.exists():
        pytest.skip("dbt project is not mounted here")
    text = project_yml.read_text()
    assert f'name: "{warehouse.DBT_PROJECT_NAME}"' in text


def test_the_dbt_binary_is_resolvable():
    """Shutil.which with a fallback: fails loudly in CI rather than at run time on a schedule."""
    assert warehouse.dbt_executable_path().endswith("dbt")
