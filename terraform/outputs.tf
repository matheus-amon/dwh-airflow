output "instance_id" {
  description = "OCID of the instance."
  value       = oci_core_instance.this.id
}

output "public_ip" {
  description = "Public IP address of the instance."
  value       = oci_core_instance.this.public_ip
}

output "ssh_command" {
  description = "Ready-to-paste SSH command."
  value       = "ssh opc@${oci_core_instance.this.public_ip}"
}

output "airflow_ui" {
  description = "URL of the Airflow UI, or null when the port is left closed."
  value       = var.airflow_ui_port == null ? null : "http://${oci_core_instance.this.public_ip}:${var.airflow_ui_port}"
}

output "warehouse_postgres_port" {
  description = <<-EOT
    Port the warehouse Postgres listens on, forwarded from the host. 5435 avoids colliding with
    Airflow's own metadata Postgres on 5432 and the generator's 5434 in a local setup.
  EOT
  value       = 5435
}

output "free_tier_headroom" {
  description = <<-EOT
    Monthly consumption against the Always Free allowance, at continuous operation. Kept as an
    output because the headroom is small and a silent increase in shape size disables the
    instance rather than costing money.
  EOT
  value = {
    ocpu_hours_used  = var.ocpus * 720
    ocpu_hours_allow = 1500
    gb_hours_used    = var.memory_gb * 720
    gb_hours_allow   = 9000
  }
}
