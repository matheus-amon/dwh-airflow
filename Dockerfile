# Runtime image for the warehouse pipelines.
#
# Built on the official Airflow image rather than an Astronomer runtime: the DAGs here need
# dbt-postgres and astronomer-cosmos, and the official image plus a two-line install is
# reproducible, needs no account and no CLI to run, and can be tested in CI. `astro dev start`
# is a better developer loop if you already pay for Astronomer, but it is not required.
# Note the tag order: apache/airflow publishes slim-<version>-python<version>, not the reverse.
FROM apache/airflow:slim-3.3.2-python3.12

# dbt and Cosmos are installed into the image's own interpreter, so the dbt executable Cosmos
# invokes is the same one a shell in the container would run. No separate virtualenv to keep in
# step with the adapter.
#
# The dbt versions are pinned to exactly what dwh-dbt/requirements.txt pins. A different dbt in
# the image than in the warehouse's CI would mean the pipeline validates against one parser and
# runs against another, and the failure would only surface in production.
RUN pip install --no-cache-dir \
      "astronomer-cosmos==1.15.1" \
      "dbt-core==1.12.5" \
      "dbt-postgres==1.11.0"

# Shared library code used by the DAGs. Kept outside dags/ so Airflow does not try to parse it
# as a DAG file on every scheduler tick.
ENV PYTHONPATH=/opt/airflow/include

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# The slim image leaves AIRFLOW_HOME owned by root, so the airflow user cannot create airflow.cfg
# or the logs directory and every process fails at startup with a PermissionError that mentions
# neither Airflow nor Docker. Fixed here rather than by running as root.
RUN mkdir -p "${AIRFLOW_HOME}/logs" "${AIRFLOW_HOME}/plugins" \
 && chown -R airflow:root "${AIRFLOW_HOME}"
