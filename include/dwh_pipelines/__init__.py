"""Shared configuration for the warehouse pipelines.

Lives outside ``dags/`` so Airflow does not try to parse it as a DAG file on every scheduler
tick. It is importable because the Dockerfile puts this directory on PYTHONPATH.
"""
