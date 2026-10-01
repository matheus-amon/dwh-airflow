locals {
  # Root compartment of the tenancy when none is given.
  compartment_id = coalesce(var.compartment_ocid, data.oci_identity_tenancy.this.id)

  # Only 22 is open to the world by default. The UI port is only opened when asked for.
  ingress_rules = concat(
    [
      {
        description = "SSH"
        source_cidr = var.allowed_ssh_cidr
        protocol    = "6" # TCP
        port        = "22"
      },
    ],
    var.airflow_ui_port == null ? [] : [
      {
        description = "Airflow UI"
        source_cidr = "0.0.0.0/0"
        protocol    = "6"
        port        = tostring(var.airflow_ui_port)
      },
    ],
  )
}

data "oci_identity_tenancy" "this" {
  tenancy_id = var.tenancy_ocid
}

data "oci_core_images" "base" {
  compartment_id           = local.compartment_id
  operating_system         = var.image_operating_system
  operating_system_version = var.image_os_version
  shape                    = var.shape
  state                    = "available"

  filter {
    name   = "sort_by"
    values = ["TIMECREATED DESC"]
  }

  filter {
    name   = "limit"
    values = ["1"]
  }
}

# A VCN with a public subnet. Public-subnet instances get outbound internet access without a
# NAT gateway, which matters because NAT is billable and is not in the Always Free allowance.
# Inbound works through the security list rules below.
resource "oci_core_vcn" "this" {
  compartment_id = local.compartment_id
  display_name   = "${var.display_name}-vcn"
  cidr_blocks    = ["10.0.0.0/16"]
  dns_label      = "warehouse"
  freeform_tags  = var.tags
}

resource "oci_core_internet_gateway" "this" {
  compartment_id = local.compartment_id
  display_name   = "${var.display_name}-igw"
  enabled        = true
  vcn_id         = oci_core_vcn.this.id
  freeform_tags  = var.tags
}

resource "oci_core_route_table" "public" {
  compartment_id = local.compartment_id
  display_name   = "${var.display_name}-public-rt"
  vcn_id         = oci_core_vcn.this.id

  route_rules {
    destination       = "0.0.0.0/0"
    destination_type  = "CIDR_BLOCK"
    network_entity_id = oci_core_internet_gateway.this.id
  }
  freeform_tags = var.tags
}

resource "oci_core_subnet" "public" {
  compartment_id             = local.compartment_id
  vcn_id                     = oci_core_vcn.this.id
  cidr_block                 = "10.0.0.0/24"
  display_name               = "${var.display_name}-public-sn"
  dns_label                  = "warehouse"
  route_table_id             = oci_core_route_table.public.id
  security_list_ids          = [oci_core_security_list.public.id]
  prohibit_public_ip_on_vnic = false
  freeform_tags              = var.tags
}

resource "oci_core_security_list" "public" {
  compartment_id = local.compartment_id
  vcn_id         = oci_core_vcn.this.id
  display_name   = "${var.display_name}-sl"

  dynamic "ingress_security_rules" {
    for_each = local.ingress_rules
    content {
      description = ingress_security_rules.value.description
      source      = ingress_security_rules.value.source_cidr
      protocol    = ingress_security_rules.value.protocol
      tcp_options {
        min = ingress_security_rules.value.port
        max = ingress_security_rules.value.port
      }
    }
  }

  egress_security_rules {
    protocol    = "6"
    destination = "0.0.0.0/0"
    tcp_options {
      min = 1
      max = 65535
    }
  }
  egress_security_rules {
    protocol    = "1" # ICMP, so traceroute and MTU discovery work
    destination = "0.0.0.0/0"
  }

  freeform_tags = var.tags
}

resource "oci_core_instance" "this" {
  compartment_id      = local.compartment_id
  display_name        = var.display_name
  shape               = var.shape
  availability_domain = data.oci_identity_availability_domains.this.availability_domains[0].name

  # Flexible shape: memory per OCPU must be a multiple of 1 GB and 1/16 <= GB/OCPU <= 64.
  source_details {
    source_type = "image"
    source_id   = data.oci_core_images.base.images[0].id
    # Lives inside source_details in provider v6, not at the resource root.
    boot_volume_size_in_gbs = var.boot_volume_gb
  }

  shape_config {
    ocpus         = var.ocpus
    memory_in_gbs = var.memory_gb
  }

  metadata = {
    ssh_authorized_keys = trimspace(file(var.ssh_public_key_path))
    ssh_host_keys       = trimspace(file(var.ssh_public_key_path))
  }

  create_vnic_details {
    assign_public_ip = true
    subnet_id        = oci_core_subnet.public.id
  }

  preserve_boot_volume = false

  freeform_tags = var.tags
}

data "oci_identity_availability_domains" "this" {
  compartment_id = local.compartment_id
}
