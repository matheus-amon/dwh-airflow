"""How the DAGs find dbt, and which warehouse they point at.

Every DAG gets its environment from here rather than building its own ``ProfileConfig``, which is
what stops two DAGs from quietly disagreeing about the target. The environment is resolved from
the ``DWH_ENVIRONMENT`` variable, set once for the whole Airflow deployment — it is a property of
where Airflow is running, not of an individual run.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass

from cosmos import ExecutionConfig, ProfileConfig, ProjectConfig
from cosmos.profiles import PostgresUserPasswordProfileMapping

#: Where ../dwh-dbt/data_warehouse is mounted in the image. See docker-compose.yml.
DBT_PROJECT_PATH = "/opt/airflow/dbt/saas_metrics_dw"

#: The dbt project name in dbt_project.yml. Must match, or Cosmos builds a different project.
DBT_PROJECT_NAME = "saas_metrics_dw"

VALID_ENVIRONMENTS = ("dev", "prod")

#: Schema the dbt project's `+schema: marts` lands models in, courtesy of the
#: generate_schema_name override. Relations are referenced as `<schema>.<name>` inside a
#: connection to the database — never as `<database>.<schema>.<name>`, which Postgres reads as a
#: cross-database reference and refuses.
MART_SCHEMA = "marts"


@dataclass(frozen=True)
class WarehouseEnvironment:
    """One target the pipelines can be pointed at."""

    name: str
    #: Airflow Connection carrying the credentials. Cosmos reads host, port, user and password
    #: from it, so those never appear in this repository.
    connection_id: str
    #: Used only to build asset URIs and log messages, never to connect.
    host: str
    port: int
    database: str
    schema: str = "public"
    threads: int = 4


ENVIRONMENTS: dict[str, WarehouseEnvironment] = {
    "dev": WarehouseEnvironment(
        name="dev",
        connection_id="warehouse_postgres",
        host=os.getenv("WAREHOUSE_HOST", "host.docker.internal"),
        port=int(os.getenv("WAREHOUSE_PORT", "5434")),
        database=os.getenv("WAREHOUSE_DB", "saas_dw"),
        threads=4,
    ),
    "prod": WarehouseEnvironment(
        name="prod",
        connection_id="warehouse_postgres_prod",
        # No default host: pointing a deployment at production should require being told where,
        # not fall through to a development default.
        host=os.getenv("WAREHOUSE_PROD_HOST", ""),
        port=int(os.getenv("WAREHOUSE_PROD_PORT", "5432")),
        database=os.getenv("WAREHOUSE_PROD_DB", "saas_dw"),
        threads=8,
    ),
}


def resolve_environment(name: str | None = None) -> WarehouseEnvironment:
    """Resolve the target environment.

    An absent value falls back to ``dev`` so a fresh checkout runs without configuration. A value
    that is present but unrecognised raises instead: silently falling back would mean a typo in
    production quietly pointing the warehouse at development.
    """
    raw = (name or os.getenv("DWH_ENVIRONMENT") or "dev").strip().lower()

    if raw not in ENVIRONMENTS:
        raise ValueError(
            f"DWH_ENVIRONMENT={raw!r} is not one of {VALID_ENVIRONMENTS}. "
            "Set it in the Airflow deployment, not on an individual DAG."
        )

    return ENVIRONMENTS[raw]


def dbt_executable_path() -> str:
    """Absolute path to the dbt binary.

    Resolved with ``which`` rather than hardcoded, so the same code works in the image, in a
    virtualenv and on a developer machine.
    """
    return shutil.which("dbt") or "/usr/local/bin/dbt"


def project_config() -> ProjectConfig:
    return ProjectConfig(
        dbt_project_path=DBT_PROJECT_PATH,
        project_name=DBT_PROJECT_NAME,
    )


def execution_config() -> ExecutionConfig:
    return ExecutionConfig(
        dbt_executable_path=dbt_executable_path(),
        # dbt is installed into the image's own interpreter, so the profile Cosmos writes and the
        # profile a shell in the container would run are the same one. Nothing creates a virtualenv
        # to fall out of step with the adapter.
        install_dbt_deps=False,
    )


def profile_config(environment: WarehouseEnvironment) -> ProfileConfig:
    """Build the Cosmos profile mapping for an environment.

    The confusing part, worth stating once: Cosmos maps dbt's ``dbname`` from the Airflow
    Connection's **schema** field, and takes the Postgres schema from ``profile_args``. So the
    connection's schema must hold the *database* name, and ``schema`` below sets the Postgres
    schema inside it. Getting these the wrong way round produces a connection error naming a
    database that was never meant to exist.

    Credentials never appear here — host, port, user and password all come from the Connection.
    """
    return ProfileConfig(
        profile_name=DBT_PROJECT_NAME,
        target_name=environment.name,
        profile_mapping=PostgresUserPasswordProfileMapping(
            conn_id=environment.connection_id,
            profile_args={
                "schema": environment.schema,
                "threads": environment.threads,
            },
        ),
    )


def mart_relation(environment: WarehouseEnvironment, name: str) -> str:
    """Schema-qualified mart relation, as usable in SQL against ``environment.database``.

    Two parts only. Adding the database in front — ``saas_dw.marts.x`` — works, but writing the
    Postgres *schema* in front (``public.marts.x``) makes Postgres read ``public`` as a database
    name and fail with "cross-database references are not implemented", which names nothing about
    the mistake.
    """
    return f"{MART_SCHEMA}.{name}"


def asset_uri(environment: WarehouseEnvironment, relation: str) -> str:
    """A stable URI for a warehouse relation, used to declare Airflow Assets.

    Deliberately not a connection string: a URI containing the production hostname ends up in
    the Airflow UI and in task logs, and the host is not what identifies the dataset.
    """
    return f"warehouse://{environment.database}/{relation}"
