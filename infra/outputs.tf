output "fetcher_name" {
  value = google_compute_instance.fetcher.name
}

output "zone" {
  value = var.zone
}

output "archive_bucket" {
  value = google_storage_bucket.archive.name
}

output "api_url" {
  description = "The recorder API's base URL (docs/api.md)."
  value       = google_cloud_run_v2_service.api.uri
}

output "api_image_repository" {
  description = "Where `just api-deploy` pushes the API's images."
  value       = "${google_artifact_registry_repository.api.location}-docker.pkg.dev/${var.project}/${google_artifact_registry_repository.api.repository_id}"
}

output "api_builds_bucket" {
  value = google_storage_bucket.builds.name
}

output "api_builder_service_account" {
  value = google_service_account.api_builder.id
}

output "identity_api_key" {
  description = "The iOS app's Identity Platform API key (`just ios-config`)."
  value       = google_apikeys_key.ios.key_string
  sensitive   = true
}

output "google_ios_client_id" {
  value = var.google_ios_client_id
}
