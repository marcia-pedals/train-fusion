output "fetcher_name" {
  value = google_compute_instance.fetcher.name
}

output "zone" {
  value = var.zone
}

output "archive_bucket" {
  value = google_storage_bucket.archive.name
}

output "recorder_service_account" {
  value = google_service_account.recorder.email
}
