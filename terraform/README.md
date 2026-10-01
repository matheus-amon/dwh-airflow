# Terraform — Always Free deployment

Provisions one Oracle Cloud Ampere A1 instance that runs the whole stack: Postgres, dbt and
Airflow. Permanent, not a 12-month trial.

```bash
cp terraform.tfvars.example terraform.tfvars   # fill in
terraform init
terraform plan                                  # read it before applying
terraform apply

../scripts-bootstrap.sh "$(terraform output -raw public_ip)"
```

---

## Why Oracle, and not the obvious free tier

Every option was checked against its current published limits, not remembered from when it was
announced:

| Option | Verdict |
|---|---|
| **Oracle Always Free** | 2 OCPU / 12 GB ARM, never expires. Terraform provisions it. **This one.** |
| Railway Free | $1/month and **0.5 GB RAM** per service. Airflow does not start. |
| Neon Free | **0.5 GB storage**, and writes *fail* once exceeded. The warehouse is **428 MB** — it fits with ~84 MB of headroom and the next refresh breaks it. Postgres only; cannot host Airflow at all. |
| Astronomer Astro | **No free tier.** Developer is usage-based from $0.35/hr, trial 14 days. |

Neon and Railway also fail on a structural point: neither exposes a provisioning API, so
"Terraform + Railway" is a contradiction. Terraform would only be rendering config for something
someone else provisions. On OCI, `terraform apply` genuinely creates the infrastructure.

## The budget is exact, not approximate

The Always Free allowance is **1,500 OCPU-hours** and **9,000 GB-hours** per month. At continuous
operation a month costs `ocpus × 720` and `gb × 720`:

```
2 OCPU × 720h = 1,440 of 1,500   (96% used)
12 GB  × 720h = 8,640 of 9,000   (96% used)
```

96% on both. `terraform output free_tier_headroom` reports the arithmetic so it does not have to
be done by hand.

**3 OCPU / 18 GB would exceed it, and Oracle disables the instance rather than charging you.**
That is why `ocpus` and `memory_gb` are variables you should not raise — they are the ceiling,
not a starting point.

Oracle halved this allowance on **2026-06-15**, from 4 OCPU / 24 GB, without announcement, and
terminated oversized instances on 2026-08-18. The numbers above are the current ones. A free tier
you read about elsewhere may be the one that was cut.

---

## Known risks

**A1 capacity.** Instances are frequently unavailable in popular regions. This shows up as
`Out of host capacity` on apply. Retry, or pick a quieter region — the region is permanent once
the tenancy exists, so choose it deliberately.

**Idle reclaim.** Oracle may reclaim instances judged idle over a 7-day window, on utilisation
rather than uptime. A warehouse rebuilt daily should not look idle, but a stopped stack might.

**Credit card.** Required for identity verification only. Reversed hold, no charge unless you
manually upgrade.

---

## Security

The default `allowed_ssh_cidr = "0.0.0.0/0"` opens SSH to the internet, which is not a default
worth shipping. Set it to your own address in `terraform.tfvars`.

`airflow_ui_port = 8080` opens the UI to the world. `bootstrap.sh` generates a random password and
prints it once, but a UI on a public IP is a public UI. Set the variable to `null` to leave the
port closed and reach it over an SSH tunnel instead:

```bash
ssh -L 8080:localhost:8080 opc@<public-ip>
```

Nothing else is exposed. The warehouse Postgres is reached through an SSH tunnel, not a public
port.

---

## Structure

```
terraform/
  versions.tf             # provider pin; why OCI and not the alternatives
  variables.tf            # everything, with the free-tier arithmetic in the docs
  main.tf                 # VCN, public subnet, security list, A1 instance
  outputs.tf              # public IP, ssh command, free-tier headroom
  terraform.tfvars.example
scripts-bootstrap.sh      # installs docker, clones the three repos, starts the stack
```

No NAT gateway on purpose: instances in a public subnet get outbound internet without one, and
NAT is billable and outside the free allowance.

Terraform provisions the **machine**. `scripts-bootstrap.sh` deploys the **application**. They are
separate on purpose — collapsing them means a failed `apt-get` marks the instance half-created, and
every retry recreates the VM.

## Validation

`fmt`, `validate` and `shellcheck` all run in CI. `terraform validate` caught two real mistakes
while this was written: the provider is `oracle/oci` and not `oracle/oracle`, and in provider v6
`boot_volume_size_in_gbs` lives inside `source_details` rather than at the resource root.

A `terraform plan` against a real tenancy is the only check that catches provider-side changes, and
it needs credentials this repository deliberately does not hold. Run it locally before the first
apply.