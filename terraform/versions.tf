# Terraform for the Always Free deployment.
#
# Oracle Cloud is the only option here where all three of these hold at once:
# Terraform genuinely provisions the infrastructure (not just renders config for someone
# else's API), the tier never expires, and 12 GB of RAM fits Postgres + dbt + Airflow.
#
# The alternatives were checked and do not work:
#   Railway Free    $1/month and 0.5 GB RAM per service. Airflow does not start.
#   Neon Free       0.5 GB storage, and writes fail once exceeded. The warehouse is 428 MB,
#                   so it fits with ~84 MB of headroom and the next refresh breaks it.
#                   Postgres only — it cannot host Airflow at all.
#   Astro           no longer has a free tier; Developer is usage-based from $0.35/hr.
#
terraform {
  required_version = ">= 1.9"

  required_providers {
    oracle = {
      source  = "oracle/oci"
      version = ">= 6.0, < 7.0"
    }
  }
}
