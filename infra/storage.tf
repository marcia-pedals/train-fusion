# The raw archive: irreplaceable, so it can't be destroyed by terraform, and the fetcher
# can only add objects (create + read; overwriting or deleting needs a permission it lacks).
resource "google_storage_bucket" "archive" {
  name                        = var.archive_bucket
  location                    = upper(var.region)
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  # deleted objects stay recoverable for 7 days (the default, made explicit)
  soft_delete_policy {
    retention_duration_seconds = 7 * 24 * 3600
  }

  lifecycle {
    prevent_destroy = true
  }

  depends_on = [google_project_service.api]
}

resource "google_storage_bucket_iam_member" "fetcher_archive" {
  for_each = toset(["roles/storage.objectCreator", "roles/storage.objectViewer"])
  bucket   = google_storage_bucket.archive.name
  role     = each.key
  member   = google_service_account.fetcher.member
}
