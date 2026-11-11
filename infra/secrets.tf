# Every secret the project uses, one per environment variable of the same name. Terraform
# only creates the containers: values are added out of band (docs/deploy.md), so they
# never appear in the repo or in terraform state. `just pull-secrets` writes them all into
# .env.local for local runs.
variable "secrets" {
  type    = set(string)
  default = ["API_KEY_511_1", "API_KEY_511_2"]
}

resource "google_secret_manager_secret" "s" {
  for_each  = var.secrets
  secret_id = each.key
  labels    = { app = "train-fusion" }

  replication {
    auto {}
  }

  depends_on = [google_project_service.api]
}
