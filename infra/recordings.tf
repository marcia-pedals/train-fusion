# Recordings from the phone app (ios/): audio + accelerometer + gyroscope, one prefix per
# recording. A separate bucket from the archive because the app's credential lives on a
# phone: the `recorder` service account can create objects here and do nothing else
# anywhere (not read, not replace, not delete).
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

# Its key is made with `just ios-key` (not by terraform, so it never enters the state).
resource "google_service_account" "recorder" {
  account_id   = "recorder"
  display_name = "train-fusion phone recorder (upload only)"
}

resource "google_storage_bucket_iam_member" "recorder_recordings" {
  bucket = google_storage_bucket.recordings.name
  role   = "roles/storage.objectCreator"
  member = google_service_account.recorder.member
}
