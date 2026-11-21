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
  description = "Phone recordings. Must match GCSUploader.bucket in ios/TrainRecorder/GCSUploader.swift and BUCKET in phone-sensor-fusion/common/paths.py."
  type        = string
  default     = "train-fusion-recordings"
}

variable "recordings_readers" {
  description = "Members (\"user:someone@example.com\") who may read the recordings bucket (just sync-recordings, download only). Set in terraform.tfvars, which is gitignored: who they are stays out of git."
  type        = list(string)
  default     = []
}
