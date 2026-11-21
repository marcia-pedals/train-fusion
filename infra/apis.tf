locals {
  apis = [
    "compute.googleapis.com",
    "iap.googleapis.com", # ssh to the VM, which has no external IP
    "monitoring.googleapis.com",
    "secretmanager.googleapis.com",
    "storage.googleapis.com",
    # the recorder API (api.tf)
    "run.googleapis.com",
    "cloudbuild.googleapis.com",
    "artifactregistry.googleapis.com",
    "firestore.googleapis.com",
    "identitytoolkit.googleapis.com",
    "securetoken.googleapis.com",
    "apikeys.googleapis.com",
    "iam.googleapis.com",            # custom roles
    "iamcredentials.googleapis.com", # signing upload URLs (signBlob)
  ]
}

resource "google_project_service" "api" {
  for_each           = toset(local.apis)
  service            = each.key
  disable_on_destroy = false
}
