locals {
  apis = [
    "compute.googleapis.com",
    "iap.googleapis.com", # ssh to the VM, which has no external IP
    "monitoring.googleapis.com",
    "secretmanager.googleapis.com",
    "storage.googleapis.com",
  ]
}

resource "google_project_service" "api" {
  for_each           = toset(local.apis)
  service            = each.key
  disable_on_destroy = false
}
