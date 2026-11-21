# The recorder app's backend (backend/, docs/api.md): accounts (Identity Platform), ride
# sync (Firestore) and signed upload URLs for the recordings bucket, on Cloud Run.
#
# Terraform makes the service with a placeholder image; `just api-deploy` builds backend/
# with Cloud Build and points the service at the new image (which terraform then leaves
# alone). Settings (env vars) are changed here, with `just tf apply`.
#
# Done by hand, once (docs/deploy.md): the Google OAuth clients, the Apple and Google
# providers in Identity Platform.

locals {
  api_host     = trimprefix(google_cloud_run_v2_service.api.uri, "https://")
  ios_bundle   = "io.github.marcia-pedals.TrainFusion"
}

# ------------------------------------------------------------------ the service

resource "google_service_account" "api" {
  account_id   = "recorder-api"
  display_name = "train-fusion recorder API (Cloud Run)"
}

resource "google_cloud_run_v2_service" "api" {
  name     = "api"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  # public: the API does its own authentication
  invoker_iam_disabled = true

  template {
    service_account                  = google_service_account.api.email
    max_instance_request_concurrency = 40
    timeout                          = "30s"

    scaling {
      min_instance_count = 0
      max_instance_count = 2
    }

    containers {
      # replaced by `just api-deploy` (ignored below)
      image = "us-docker.pkg.dev/cloudrun/container/hello"

      resources {
        limits            = { cpu = "1", memory = "512Mi" }
        cpu_idle          = true # CPU only during requests
        startup_cpu_boost = true
      }

      dynamic "env" {
        for_each = {
          GCP_PROJECT          = var.project
          RECORDINGS_BUCKET    = google_storage_bucket.recordings.name
          MAX_UPLOAD_BYTES     = "200000000"
          ANON_UPLOADS_PER_DAY = "24"
          USER_UPLOADS_PER_DAY = "12"
          SIGNUPS_PER_DAY      = "24"
          CONTACT_EMAIL        = var.contact_email
        }
        content {
          name  = env.key
          value = env.value
        }
      }
    }
  }

  lifecycle {
    ignore_changes = [
      template[0].containers[0].image, # `just api-deploy`
      client,
      client_version,
    ]
  }

  depends_on = [
    google_project_service.api,
    google_firestore_database.default,
    google_storage_bucket_iam_member.api_recordings_create,
    google_storage_bucket_iam_member.api_recordings_list,
  ]
}

# ------------------------------------------------------------------ what the service may do

# Hand out upload URLs: they're signed as the service account (IAM signBlob, there's no
# key), so a PUT through one has the account's permissions: create objects under raw/,
# nothing else (not read, replace or delete).
resource "google_storage_bucket_iam_member" "api_recordings_create" {
  bucket = google_storage_bucket.recordings.name
  role   = "roles/storage.objectCreator"
  member = google_service_account.api.member

  condition {
    title      = "raw-only"
    expression = "resource.name.startsWith(\"projects/_/buckets/${google_storage_bucket.recordings.name}/objects/raw/\")"
  }
}

resource "google_service_account_iam_member" "api_sign_as_itself" {
  service_account_id = google_service_account.api.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = google_service_account.api.member
}

# /complete checks the files arrived at their sizes: listing gives names and sizes, without
# being able to read the recordings.
resource "google_project_iam_custom_role" "recordings_lister" {
  role_id     = "recordingsLister"
  title       = "List objects (names and sizes only)"
  permissions = ["storage.objects.list"]
}

resource "google_storage_bucket_iam_member" "api_recordings_list" {
  bucket = google_storage_bucket.recordings.name
  role   = google_project_iam_custom_role.recordings_lister.id
  member = google_service_account.api.member
}

resource "google_project_iam_member" "api_firestore" {
  project = var.project
  role    = "roles/datastore.user"
  member  = google_service_account.api.member
}

# Deleting accounts (account deletion, and refused signups). Blocking is done from a
# laptop with your own credentials (`just block-account`).
resource "google_project_iam_custom_role" "account_deleter" {
  role_id     = "accountDeleter"
  title       = "Delete Identity Platform users"
  permissions = ["firebaseauth.users.delete"]
}

resource "google_project_iam_member" "api_delete_accounts" {
  project = var.project
  role    = google_project_iam_custom_role.account_deleter.id
  member  = google_service_account.api.member
}

# ------------------------------------------------------------------ Firestore

resource "google_firestore_database" "default" {
  name                    = "(default)"
  location_id             = var.region
  type                    = "FIRESTORE_NATIVE"
  delete_protection_state = "DELETE_PROTECTION_ENABLED"
  deletion_policy         = "ABANDON"

  lifecycle {
    prevent_destroy = true
  }

  depends_on = [google_project_service.api]
}

# ------------------------------------------------------------------ Identity Platform

# Email link sign-in (passwordless). Apple and Google are added by hand (docs/deploy.md):
# their secrets stay out of terraform state, as with secrets.tf.
resource "google_identity_platform_config" "default" {
  authorized_domains = [
    "localhost",
    "${var.project}.firebaseapp.com",
    "${var.project}.web.app",
    local.api_host, # the emailed link's continue URL
  ]

  sign_in {
    allow_duplicate_emails = false
    email {
      enabled           = true
      password_required = false
    }
  }

  depends_on = [google_project_service.api]
}

# The app's key for Identity Platform (sign-in and token refresh). Not a secret (it ships
# in the app), but restricted to those two APIs, from the iOS app.
resource "google_apikeys_key" "ios" {
  name         = "recorder-ios"
  display_name = "Recorder app (iOS): Identity Platform"

  restrictions {
    ios_key_restrictions {
      allowed_bundle_ids = [local.ios_bundle]
    }
    api_targets {
      service = "identitytoolkit.googleapis.com"
    }
    api_targets {
      service = "securetoken.googleapis.com"
    }
  }

  depends_on = [google_project_service.api]
}

# ------------------------------------------------------------------ building (`just api-deploy`)

resource "google_artifact_registry_repository" "api" {
  repository_id = "api"
  location      = var.region
  format        = "DOCKER"

  cleanup_policies {
    id     = "keep-recent"
    action = "KEEP"
    most_recent_versions {
      keep_count = 5
    }
  }
  cleanup_policies {
    id     = "delete-old"
    action = "DELETE"
    condition {
      older_than = "${30 * 24 * 3600}s"
    }
  }

  depends_on = [google_project_service.api]
}

# Cloud Build's source uploads (`gcloud builds submit --gcs-source-staging-dir`).
resource "google_storage_bucket" "builds" {
  name                        = "${var.project}-api-builds"
  location                    = upper(var.region)
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = true

  lifecycle_rule {
    condition {
      age = 7
    }
    action {
      type = "Delete"
    }
  }

  depends_on = [google_project_service.api]
}

# Builds run as this account: it reads the source and pushes the image, nothing else.
resource "google_service_account" "api_builder" {
  account_id   = "api-builder"
  display_name = "train-fusion recorder API builds (Cloud Build)"
}

resource "google_storage_bucket_iam_member" "api_builder_source" {
  bucket = google_storage_bucket.builds.name
  role   = "roles/storage.objectViewer"
  member = google_service_account.api_builder.member
}

resource "google_artifact_registry_repository_iam_member" "api_builder_push" {
  repository = google_artifact_registry_repository.api.name
  location   = google_artifact_registry_repository.api.location
  role       = "roles/artifactregistry.writer"
  member     = google_service_account.api_builder.member
}

resource "google_project_iam_member" "api_builder_logs" {
  project = var.project
  role    = "roles/logging.logWriter"
  member  = google_service_account.api_builder.member
}
