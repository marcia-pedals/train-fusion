# Recordings from the phone app (ios/): audio + motion sensors, one prefix per upload,
# raw/<recording id>-<8 random hex>/. The app holds no credential: the recorder API
# (api.tf) hands it signed URLs, each creating one object under raw/ at a declared size
# (never replacing one), and the API's service account can do no more here than that and
# list names and sizes. (Older builds upload with the `recorder` service account's key,
# below.) The rest of the bucket (labels/) is written from a laptop, with your own
# credentials (`just sync-recordings`).
resource "google_storage_bucket" "recordings" {
  name                        = var.recordings_bucket
  location                    = upper(var.region)
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  soft_delete_policy {
    retention_duration_seconds = 7 * 24 * 3600
  }

  lifecycle {
    prevent_destroy = true
  }

  depends_on = [google_project_service.api]
}

# For older builds of the app, from before the recorder API: they bundle a key for this
# account (made with `gcloud iam service-accounts keys create`, so never in terraform's
# state) and upload to raw/<recording id>/ with it. It can create objects under raw/ here
# and do nothing else anywhere (not read, not replace, not delete). Kept while phones may
# still run those builds; removing these two resources (and the output) cuts them off.
resource "google_service_account" "recorder" {
  account_id   = "recorder"
  display_name = "train-fusion phone recorder (upload only)"
}

resource "google_storage_bucket_iam_member" "recorder_recordings" {
  bucket = google_storage_bucket.recordings.name
  role   = "roles/storage.objectCreator"
  member = google_service_account.recorder.member

  condition {
    title      = "raw-only"
    expression = "resource.name.startsWith(\"projects/_/buckets/${google_storage_bucket.recordings.name}/objects/raw/\")"
  }
}

# Read-only access for others who run `just sync-recordings`: list and download raw/ and
# labels/, but not create, replace or delete anything. Who they are is set in
# terraform.tfvars (gitignored), e.g. recordings_readers = ["user:someone@example.com"].
resource "google_storage_bucket_iam_member" "recordings_readers" {
  for_each = toset(var.recordings_readers)
  bucket   = google_storage_bucket.recordings.name
  role     = "roles/storage.objectViewer"
  member   = each.value
}
