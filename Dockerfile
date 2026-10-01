# Runtime image for the warehouse pipelines.
#
# Built on the official Airflow image rather than an Astronomer runtime: the DAGs here need
# dbt-postgres and astronomer-cosmos, and the official image plus a two-line install is
# reproducible, needs no account and no CLI to run, and can be tested in CI. `astro dev start`
# is a better developer loop if you already pay for Astronomer, but it is not required.
FROM apache/airflow:3.3.2-slim-python3.12

ARG AIRFLOW_VERSION=3.3.2

# dbt and Cosmos are installed into the image's own interpreter, so the dbt executable Cosmos
# invokes is the same one a shell in the container would run. No separate virtualenv to keep in
# step with the adapter.
RUN pip install --no-cache-dir \
      "astronomer-cosmos==1.15.1" \
      "dbt-postgres==1.12.*"

# Shared library code used by the DAGs. Kept outside dags/ so Airflow does not try to parse it
# as a DAG file on every scheduler tick.
ENV PYTHONPATH=/usr/local/airflow/include

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
