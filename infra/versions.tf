terraform {
  required_version = ">= 1.6"

  # Created once by hand (it can't manage itself): see docs/deploy.md.
  backend "gcs" {
    bucket = "train-fusion-tfstate"
    prefix = "main"
  }

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 7.0"
    }
  }
}

provider "google" {
  project = var.project
  region  = var.region
  zone    = var.zone

  # Bill API quota to this project, not to gcloud's own: with your own login (application
  # default credentials), some APIs (Identity Platform's) refuse to run otherwise.
  user_project_override = true
  billing_project       = var.project
}
