# Nicer public URLs on the project's default Firebase Hosting site, https://<project>.web.app:
# the privacy policy is https://<project>.web.app/privacy (what the app, App Store Connect
# and Google's consent screen link to). Hosting holds no files: it passes these paths on to
# the API service (api.tf), which serves the pages (backend/api/pages.py).
#
# The site itself already exists (every Firebase project has one; Identity Platform's sign-in
# pages live under its reserved /__/ paths, which releases here don't touch).
locals {
  hosting_site = var.project
}

resource "google_firebase_hosting_version" "site" {
  provider = google-beta
  site_id  = local.hosting_site

  config {
    rewrites {
      glob = "/privacy"
      run {
        service_id = google_cloud_run_v2_service.api.name
        region     = var.region
      }
    }
    # the app icon, for browser tabs and home screens (backend/api/static)
    dynamic "rewrites" {
      for_each = ["/favicon.ico", "/icon-192.png", "/apple-touch-icon.png"]
      content {
        glob = rewrites.value
        run {
          service_id = google_cloud_run_v2_service.api.name
          region     = var.region
        }
      }
    }
    redirects {
      glob        = "/"
      status_code = 302
      location    = "/privacy"
    }
  }
}

resource "google_firebase_hosting_release" "site" {
  provider     = google-beta
  site_id      = local.hosting_site
  version_name = google_firebase_hosting_version.site.name
  message      = "terraform (infra/hosting.tf)"
}
