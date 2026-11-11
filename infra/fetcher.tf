# The archiver: fetch.py on one small VM, uploading to the archive bucket.
#
# Deploying = `just tf apply`. The code is baked into the startup script, so any change to
# fetch.py / fetch_cloud.py / the script replaces the VM: the new one is created before the
# old one is deleted, and on deletion the old one uploads everything it has before stopping.
# Running both for a minute or two is harmless (the archive merges by union, and the 511
# quota tolerates it).

locals {
  # Secrets (see secrets.tf) the fetcher reads into its environment at start.
  fetcher_secrets = ["API_KEY_511_1", "API_KEY_511_2"]

  startup_script = templatefile("${path.module}/fetcher/startup.sh.tftpl", {
    project        = var.project
    bucket         = google_storage_bucket.archive.name
    secrets        = join(" ", local.fetcher_secrets)
    requirements   = filebase64("${path.module}/fetcher/requirements.txt")
    fetch_py       = filebase64("${path.module}/../fetch.py")
    fetch_cloud_py = filebase64("${path.module}/../fetch_cloud.py")
  })
}

resource "google_service_account" "fetcher" {
  account_id   = "fetcher"
  display_name = "train-fusion archiver"
}

resource "google_secret_manager_secret_iam_member" "fetcher" {
  for_each  = toset(local.fetcher_secrets)
  secret_id = google_secret_manager_secret.s[each.key].id
  role      = "roles/secretmanager.secretAccessor"
  member    = google_service_account.fetcher.member
}

resource "google_project_iam_member" "fetcher_metrics" {
  project = var.project
  role    = "roles/monitoring.metricWriter"
  member  = google_service_account.fetcher.member
}

resource "google_compute_instance" "fetcher" {
  # a new name per version, so the replacement can exist alongside the old one
  name         = "fetcher-${substr(sha256(local.startup_script), 0, 8)}"
  machine_type = "e2-micro" # free tier (in us-west1, -central1, -east1)
  zone         = var.zone
  tags         = ["iap-ssh"]

  boot_disk {
    initialize_params {
      image = "debian-cloud/debian-12"
      size  = 10
      type  = "pd-standard" # the free tier's disk type
    }
  }

  network_interface {
    subnetwork = google_compute_subnetwork.main.id
    # no access_config: no external IP (egress via Cloud NAT)
  }

  service_account {
    email  = google_service_account.fetcher.email
    scopes = ["cloud-platform"] # access is limited by IAM instead
  }

  metadata = {
    enable-oslogin = "TRUE"
  }
  metadata_startup_script = local.startup_script

  shielded_instance_config {
    enable_secure_boot = true
  }

  lifecycle {
    create_before_destroy = true
  }

  depends_on = [
    google_compute_router_nat.main,
    google_storage_bucket_iam_member.fetcher_archive,
    google_secret_manager_secret_iam_member.fetcher,
    google_project_iam_member.fetcher_metrics,
  ]
}
