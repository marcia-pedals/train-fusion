variable "project" {
  type    = string
  default = "train-fusion"
}

# us-west1: near 511's servers, and in the e2-micro free tier.
variable "region" {
  type    = string
  default = "us-west1"
}

variable "zone" {
  type    = string
  default = "us-west1-b"
}

variable "archive_bucket" {
  description = "The raw archive. Must match ARCHIVE_BUCKET in train_fusion/common/paths.py."
  type        = string
  default     = "train-fusion-archive"
}

variable "alert_email" {
  description = "Where alerts go. Set in infra/terraform.tfvars (gitignored)."
  type        = string
}

variable "recordings_bucket" {
  description = "Phone recordings. Must match BUCKET in phone-sensor-fusion/common/paths.py. (The app doesn't know it: the API hands it signed URLs.)"
  type        = string
  default     = "train-fusion-recordings"
}

variable "recordings_readers" {
  description = "Members (\"user:someone@example.com\") who may read the recordings bucket (just sync-recordings, download only). Set in terraform.tfvars, which is gitignored: who they are stays out of git."
  type        = list(string)
  default     = []
}

variable "contact_email" {
  description = "The contact address on the API's privacy policy (/privacy). Set in terraform.tfvars (gitignored)."
  type        = string
}

variable "google_ios_client_id" {
  description = "The iOS OAuth client id for Sign in with Google (made by hand: docs/deploy.md), e.g. 123-abc.apps.googleusercontent.com. Set in terraform.tfvars; `just ios-config` writes it into the app's config."
  type        = string
  default     = ""
}
