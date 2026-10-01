# dwh-airflow

Orchestration. Airflow 3 runs the warehouse in [`dwh-dbt`](https://github.com/matheus-amon/dwh-dbt)
and consumes its output as an Asset.

| Repo | Role |
|---|---|
| [`dwh-config-local`](https://github.com/matheus-amon/dwh-config-local) | Generates the synthetic raw data and owns the Postgres the warehouse lives in |
| [`dwh-dbt`](https://github.com/matheus-amon/dwh-dbt) | The warehouse: staging, core, marts, and 300 tests |
| **`dwh-airflow`** (this one) | Runs the warehouse daily, and reports on it when it lands |

## The two pipelines

### `dbt_daily_build` — cron, builds everything

The task graph comes entirely from the dbt project: **313 nodes**, one per model and per test,
with the dependencies dbt already knows about. Adding a model to `dwh-dbt` adds it to this DAG
with no change here.

`RenderConfig` leaves `test_behavior` at `after-each`, so a failed data test fails the DAG run
instead of reporting success and moving on. That is the difference between a green DAG and a
correct one.

Environment targeting comes from the profile's `target_name`, not from a dbt node selection.
Repointing this deployment at production changes one variable and nothing else.

### `product_usage_rollup` — Asset-driven, reports on the mart

Subscribes to `marts.mart_product_adoption` as an Airflow 3 Asset, so it runs when the mart has
actually been rebuilt. On a cron it could read a mart that has not been rebuilt and publish a
stale report with nothing signalling it.

The task reads the latest complete month, compares it against the previous one, and writes a
dated CSV. The change is expressed in **percentage points**, not as a ratio of rates, because a
ratio invites reading a 10-point drop as a 90% drop.

## Running it

Needs the warehouse Postgres first, from the generator repo:

```bash
# terminal 1 — the warehouse's database and raw data
cd ../dwh-config-local
docker compose -f docker-compose.telemetry.yml up -d
set -a && . ./.env.telemetry && set +a
python -m src.telemetry_lab.cli generate --output-dir data/raw
python -m src.telemetry_lab.cli load

# terminal 2 — Airflow
docker compose up -d
docker compose logs -f airflow-init      # migrates the DB, creates the connection
open http://localhost:8080               # admin / admin
```

Run one DAG end to end:

```bash
docker compose exec airflow-scheduler airflow dags test dbt_daily_build
docker compose exec airflow-scheduler airflow dags reserialize   # after editing a DAG
docker compose exec airflow-scheduler airflow dags test product_usage_rollup
```

**`dags reserialize` after editing a DAG.** `dags test` runs the DAG serialised in the metadata
database, not the file on disk. Editing the file and re-running serves the previous version, and
the symptom is a test that passes against code you already changed — or a fix that appears not to
work.

The tests:

```bash
docker compose exec airflow-scheduler pytest tests -q
```

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `DWH_ENVIRONMENT` | `dev` | `dev` or `prod`. Anything else raises rather than falling back. |
| `AIRFLOW_FERNET_KEY` | dev key | **Set this in any real deployment.** |
| `WAREHOUSE_HOST` / `WAREHOUSE_PORT` | `host.docker.internal` / `5434` | Where the warehouse Postgres is. |
| `AIRFLOW_POSTGRES_PORT` | `5435` | Airflow's own metadata Postgres. |
| `DWH_EXPORT_DIR` | `/opt/airflow/exports` | Where the rollup writes. |

`DWH_ENVIRONMENT` is an OS environment variable, deliberately not an Airflow Variable. A Variable
can be edited from the UI between the scheduler parsing and a task running, which is how a
deployment ends up half dev and half prod. An OS variable is set once for the whole deployment and
every process agrees on it.

## Layout

```
Dockerfile                  # apache/airflow + dbt + cosmos, all pinned
docker-compose.yml          # metadata Postgres, init, api-server, dag-processor, scheduler, triggerer
dags/
  dbt_daily_build.py        # Cosmos, builds the whole warehouse
  product_usage_rollup.py   # Asset-driven, writes the adoption report
include/dwh_pipelines/
  warehouse.py              # shared: environments, dbt wiring, asset URIs
tests/dags/
  test_dags.py              # DAG integrity and metadata
  test_warehouse_config.py  # environment resolution, pure and Airflow-free
```

`include/` rather than a helper module inside `dags/`, because Airflow imports every Python file
under `dags/` on each scheduler tick and would treat a library as a DAG file.

## Things that are easy to get wrong here

Recorded because each one cost a real debugging session, and each fails with an error that points
somewhere other than the cause.

**Cosmos maps dbt's `dbname` from the Connection's `schema` field.** The Postgres schema comes
from `profile_args` instead. So the Connection's schema must hold the *database* name. Putting
`public` there points dbt at a database called `public`, and the error names a database that was
never meant to exist.

**Every Airflow process needs the same Fernet key.** Each container generates its own in
`airflow.cfg`, and `AIRFLOW_HOME` is not a shared volume, so without an explicit
`AIRFLOW__CORE__FERNET_KEY` the connection password that `airflow-init` encrypted cannot be
decrypted by the scheduler. The symptom is a task failing with *the conn_id isn't defined* — which
sends you looking for the connection instead of the key.

**Airflow 3 removed `airflow webserver` and `airflow users create`.** The UI comes from
`api-server`, and users come from `[core] simple_auth_manager_users`. There is also a separate
`dag-processor` process.

**`dag` from `airflow.sdk` returns a factory, not a context manager.** The Airflow 2
`with dag(...)` shape raises `TypeError: 'function' object does not support the context manager
protocol`. Use the decorator and call the result.

**A Cosmos DAG legitimately takes minutes to import.** It runs `dbt deps` and `dbt ls` while the
file is being parsed. Airflow's 30-second default reports an import timeout, which reads as a
broken DAG file. Raised to 180s here.

**`host.docker.internal` does not exist on Linux.** Without `extra_hosts:
["host.docker.internal:host-gateway"]` the warehouse connection fails with a DNS error that
mentions nothing about Docker.

**A missing bind-mount source becomes an empty root-owned directory.** Docker creates it rather
than erroring, so the stack starts with no DAGs and no complaint. Worth knowing the first time
`../dwh-dbt` is renamed.

## Why not Astronomer

The image is the official `apache/airflow` plus two pinned pip installs, rather than the Astro
Runtime. `astro dev start` is a better loop if you already pay for Astronomer — but it needs an
account and a CLI, which means the image cannot be built in CI and the DAGs cannot be tested there.
This repo's CI builds the image and runs the DAG tests, which seemed the more useful property.

## Tests

19 tests. `test_dags.py` asserts the DAGs parse, are scheduled, carry tags from an approved set,
retry at least twice, declare one `dag_id` each, and state their target environment — plus that
the Cosmos DAG actually expanded the dbt project into tasks, which is the check that catches a
mount that silently points at nothing.

`test_warehouse_config.py` covers the pure functions, including the failure path: an unrecognised
`DWH_ENVIRONMENT` must raise rather than fall back to dev, because silently pointing a production
deployment at development is the worst available outcome.

## Lineage

Independent implementation. Inspired by Airflow/dbt workshop material the author worked through —
a local-setup repo, a dbt warehouse, and an Airflow orchestration repo — and reusing no code from
it. The structure, the DAGs and the configuration here are original to this project.

## Licence

MIT