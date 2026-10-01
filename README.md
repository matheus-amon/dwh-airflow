# dwh-airflow

Airflow 3 + Cosmos runs the [`dwh-dbt`](https://github.com/matheus-amon/dwh-dbt) warehouse daily
and consumes its output as an Asset.

| | |
|---|---|
| **Airflow** | 3.3.2 · `astronomer-cosmos` 1.15.1 · dbt 1.12 |
| **Pipelines** | `dbt_daily_build` (cron) · `product_usage_rollup` (Asset-driven) |
| **Build** | `dbt_daily_build` expands to **313 tasks** — one per dbt model and test |
| **Verified** | Ran end to end against the real warehouse: `state=success`, 407 s |
| **Tests** | 19, in CI against the built image |

Part of a three-repo stack:

| Repo | Role |
|---|---|
| [`dwh-config-local`](https://github.com/matheus-amon/dwh-config-local) | Generates the synthetic raw data and owns the Postgres it lands in |
| [`dwh-dbt`](https://github.com/matheus-amon/dwh-dbt) | The warehouse: staging, core, marts, 308 tests |
| **`dwh-airflow`** (this one) | Runs the warehouse, and reports on it when it lands |

---

## The two pipelines

### `dbt_daily_build` — cron, builds everything

The task graph comes entirely from the dbt project: 313 nodes, one per model and per test, with the
dependencies dbt already knows about. **Adding a model to `dwh-dbt` adds it to this DAG with no
change here** — nothing about the warehouse's shape is duplicated in this repository.

`RenderConfig` leaves `test_behavior` at `after-each`, so a failed data test **fails the DAG run**
rather than reporting success and moving on. That is the difference between a green DAG and a
correct one, and it is the default rather than something left implicit.

Environment targeting comes from the profile's `target_name`, not from a dbt node selection, so
repointing a deployment at production changes one variable.

Verified end to end, not just parsed:

```
16 models, 292 data tests, 5 sources, 599 macros
state=success   run_duration=407s
```

### `product_usage_rollup` — Asset-driven, reports on the mart

Subscribes to `marts.mart_product_adoption` as an Airflow 3 Asset, so it runs when the mart has
actually been rebuilt. On a cron it could read a mart nobody has rebuilt and publish a stale report
with nothing signalling it.

```
month      feature         tier          entitled  adoption   Δ vs prev month
2026-09    core_dashboard  starter          5,372       43.4%            +1.8pp
2026-09    core_dashboard  enterprise      6,982       77.4%            -1.4pp
2026-09    sso             scale           7,042       41.0%            -4.9pp
2026-09    advanced_analytics enterprise    6,982       49.7%            -2.6pp
```

The change is in **percentage points**, not as a ratio of rates, because a ratio invites reading a
10-point drop as a 90% drop.

---

## Running it

Needs the warehouse Postgres first:

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

Run one pipeline end to end:

```bash
docker compose exec airflow-scheduler airflow dags test dbt_daily_build
docker compose exec airflow-scheduler airflow dags test product_usage_rollup
docker compose exec airflow-scheduler pytest tests -q
```

**`airflow dags reserialize` after editing a DAG.** `dags test` runs the DAG *serialised in the
metadata database*, not the file on disk. Editing the file and re-running serves the previous
version — and the symptom is a test passing against code you already changed, or a fix that appears
not to work.

---

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `DWH_ENVIRONMENT` | `dev` | `dev` or `prod`. Anything else raises. |
| `AIRFLOW_FERNET_KEY` | dev key | **Set this in any real deployment.** |
| `WAREHOUSE_HOST` / `WAREHOUSE_PORT` | `host.docker.internal` / `5434` | Where the warehouse Postgres is. |
| `AIRFLOW_POSTGRES_PORT` | `5435` | Airflow's own metadata Postgres. |

`DWH_ENVIRONMENT` is an OS environment variable, deliberately **not** an Airflow Variable. A
Variable can be edited from the UI between the scheduler parsing and a task running, which is how a
deployment ends up half dev and half prod. An OS variable is set once for the whole deployment and
every process agrees on it.

---

## Layout

```
Dockerfile                  # apache/airflow + dbt + cosmos, all version-pinned
docker-compose.yml          # metadata Postgres, init, api-server, dag-processor, scheduler, triggerer
dags/
  dbt_daily_build.py        # Cosmos, builds the whole warehouse
  product_usage_rollup.py   # Asset-driven, writes the adoption report
include/dwh_pipelines/
  warehouse.py              # shared: environments, dbt wiring, asset URIs
tests/dags/                 # 19 tests
```

`include/` rather than a helper module inside `dags/`, because Airflow imports every Python file
under `dags/` on each scheduler tick and would treat a library as a DAG file.

The dbt project is mounted **read-only** from `../dwh-dbt`. Airflow runs it but does not own it, and
a DAG that could edit its own dependency is a DAG that can break it.

---

## Judgment calls

**The official Airflow image, not Astro Runtime.** `astro dev start` is a better developer loop if
you already pay for Astronomer — but it needs an account and a CLI, which means the image cannot be
built in CI and the DAGs cannot be tested there. This repo's CI builds the image and runs the DAG
tests, which seemed the more useful property.

**dbt versions pinned to exactly what `dwh-dbt/requirements.txt` pins.** A different dbt in the
image than in the warehouse's CI would mean the pipeline validates against one parser and runs
against another, and the failure would only surface in production.

**No virtualenv inside the image.** dbt is installed into the image's own interpreter, so the
profile Cosmos writes and the profile a shell in the container would run are the same one. Nothing
to fall out of step with the adapter.

**Shared library code outside `dags/`.** Airflow parses every `.py` under `dags/` on each tick.

**`include/` mounted rather than a DAG-bundle.** A bundle is the better abstraction but adds a
concept that buys nothing here, where the library is 130 lines.

---

## Tests

19, run in CI against the built image rather than on the runner, so they exercise the same Airflow,
Cosmos and dbt versions that would run in production.

`test_dags.py` asserts the DAGs parse, are scheduled, carry tags from an approved set, retry at
least twice, declare one `dag_id` each, and state their target environment — plus that the Cosmos
DAG actually expanded the dbt project into tasks, which catches a mount that silently points at
nothing.

`test_warehouse_config.py` covers the pure functions including the failure path: an unrecognised
`DWH_ENVIRONMENT` must raise rather than fall back to dev, because silently pointing a production
deployment at development is the worst available outcome.

---

## Configuration traps

Recorded because each cost a real debugging session, and each fails with an error pointing
somewhere other than the cause.

**Cosmos maps dbt's `dbname` from the Connection's `schema` field.** The Postgres schema comes from
`profile_args` instead. So the Connection's schema must hold the *database* name — putting `public`
there points dbt at a database called `public`.

**Every Airflow process needs the same Fernet key.** Each container generates its own in
`airflow.cfg`, and `AIRFLOW_HOME` is not a shared volume, so without an explicit
`AIRFLOW__CORE__FERNET_KEY` the connection password that `airflow-init` encrypted cannot be
decrypted by the scheduler. The symptom is a task failing with *the conn_id isn't defined* — which
sends you looking for the connection instead of the key that encrypted its password.

**Airflow 3 removed `airflow webserver` and `airflow users create`.** The UI comes from
`api-server`, users come from `[core] simple_auth_manager_users`, and there is a separate
`dag-processor` process.

**A Cosmos DAG legitimately takes minutes to import.** It runs `dbt deps` and `dbt ls` while the file
is being parsed. Airflow's 30-second default reports an import timeout, which reads as a broken DAG
file. Raised to 180 s here.

**`host.docker.internal` does not exist on Linux** without
`extra_hosts: ["host.docker.internal:host-gateway"]`.

**A missing bind-mount source becomes an empty root-owned directory.** Docker creates it rather
than erroring, so the stack starts with no DAGs and no complaint.

**Relations are `marts.<name>`, never `public.marts.<name>`.** The profile's Postgres schema is not
the mart schema, and the mixed form makes Postgres read `public` as a *database*.

---

## Lineage

Independent implementation. Inspired by Airflow/dbt workshop material the author worked through — a
local-setup repo, a dbt warehouse, and an Airflow orchestration repo — and reusing no code from it.
The structure, the pipelines and the configuration here are original to this project.

## Licence

MIT