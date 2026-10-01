variable "region" {
  description = "OCI region. Permanent once the tenancy is created and it cannot be changed. A1 capacity varies by region — see the README."
  type        = string
  default     = "sa-saopaulo-1"
}

variable "compartment_ocid" {
  description = "Compartment OCID to create resources in. Leave null to use the root compartment of the tenancy."
  type        = string
  default     = null
}

variable "ssh_public_key_path" {
  description = "Path to the SSH public key authorised to reach the instance."
  type        = string
  default     = "~/.ssh/id_ed25519.pub"
}

variable "allowed_ssh_cidr" {
  description = <<-EOT
    CIDR allowed to reach SSH. Narrow this to your own address — the default opens 22 to the
    whole internet on a machine that will hold deployment credentials.
  EOT
  type        = string
  default     = "0.0.0.0/0"
}

variable "airflow_ui_port" {
  description = <<-EOT
    Host port for the Airflow UI. Set to null to leave it closed. bootstrap.sh generates a
    random password and prints it, but a UI on a public IP is a public UI.
  EOT
  type        = number
  default     = 8080
}

# --- Always Free shape -----------------------------------------------------------
# The allowance is 1,500 OCPU-hours and 9,000 GB-hours per month. Running continuously for a
# 30-day month costs OCPUs x 720 and GB x 720, so 2/12 consumes 1,440 and 8,640 — inside the
# budget, with very little headroom. 3/18 would be 2,160 and 12,960, and Oracle would disable
# the instance. That is why these are not variables you should raise.
variable "ocpus" {
  description = "Always Free Ampere A1 OCPUs. Budget is 2 for continuous operation."
  type        = number
  default     = 2
}

variable "memory_gb" {
  description = "Always Free Ampere A1 memory in GB. Budget is 12 for continuous operation."
  type        = number
  default     = 12
}

variable "boot_volume_gb" {
  description = "Boot volume. Oracle enforces a 47 GB minimum on A1 regardless of shape."
  type        = number
  default     = 47
}

variable "shape" {
  description = "Instance shape. VM.Standard.A1.Flex is the flexible Arm shape in the free tier."
  type        = string
  default     = "VM.Standard.A1.Flex"
}

variable "display_name" {
  description = "Name of the instance."
  type        = string
  default     = "saas-warehouse"
}

variable "image_operating_system" {
  description = "Base image to boot from."
  type        = string
  default     = "Oracle Linux"
}

variable "image_os_version" {
  description = "Major OS version of the base image."
  type        = string
  default     = "9"
}

variable "tags" {
  description = "Free-form tags applied to every resource."
  type        = map(string)
  default     = {}
}

variable "tenancy_ocid" {
  description = "OCID of the tenancy, used only to resolve the root compartment when compartment_ocid is null. Leave null and run `oci iam compartment list` to find it."
  type        = string
  default     = null
}
