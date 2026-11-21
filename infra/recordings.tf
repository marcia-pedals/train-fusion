# Recordings from the phone app (ios/): audio + motion sensors, one prefix per upload,
# raw/<recording id>-<8 random hex>/. The app holds no credential: the recorder API
# (api.tf) hands it signed URLs, each creating one object under raw/ at a declared size
# (never replacing one), and the API's service account can do no more here than that and
# list names and sizes. The rest of the bucket (labels/) is written from a laptop, with your
# own credentials (`just sync-recordings`).
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

# Read-only access for others who run `just sync-recordings`: list and download raw/ and
# labels/, but not create, replace or delete anything. Who they are is set in
# terraform.tfvars (gitignored), e.g. recordings_readers = ["user:someone@example.com"].
resource "google_storage_bucket_iam_member" "recordings_readers" {
  for_each = toset(var.recordings_readers)
  bucket   = google_storage_bucket.recordings.name
  role     = "roles/storage.objectViewer"
  member   = each.value
}
