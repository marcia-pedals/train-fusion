# Alerts, by email. Two failure modes are covered separately:
#   - nothing reaches the archive (fetcher, VM or upload dead): GCS's own write metric;
#   - a feed fails while uploads continue (e.g. a revoked key: error bodies are archived
#     too): the fetcher's count of HTTP 200 fetches per feed (fetchers/cloud.py Metrics).
# Plus a warning when two fetchers keep running at once (doubling the 511 quota use).

locals {
  fetches_metric = "custom.googleapis.com/train_fusion/fetches"
  runbook        = "Logs: `just fetcher-logs`. Redeploy: `just tf apply`. See docs/deploy.md."
}

resource "google_monitoring_notification_channel" "email" {
  display_name = "train-fusion alerts"
  type         = "email"
  labels       = { email_address = var.alert_email }
  depends_on   = [google_project_service.api]
}

resource "google_monitoring_metric_descriptor" "fetches" {
  type         = local.fetches_metric
  display_name = "train-fusion successful fetches"
  description  = "HTTP 200 fetches since the fetcher process started, per feed and process."
  metric_kind  = "CUMULATIVE"
  value_type   = "INT64"

  labels {
    key = "feed"
  }
  labels {
    key         = "instance"
    description = "fetcher process (host + random id)"
  }

  depends_on = [google_project_service.api]
}

resource "google_monitoring_alert_policy" "archive_stalled" {
  display_name          = "train-fusion: nothing written to the archive for 15 min"
  combiner              = "OR"
  notification_channels = [google_monitoring_notification_channel.email.id]

  conditions {
    display_name = "no successful writes to gs://${google_storage_bucket.archive.name}"
    condition_absent {
      filter = join(" AND ", [
        "resource.type = \"gcs_bucket\"",
        "resource.labels.bucket_name = \"${google_storage_bucket.archive.name}\"",
        "metric.type = \"storage.googleapis.com/api/request_count\"",
        "metric.labels.method = \"WriteObject\"",
        "metric.labels.response_code = \"OK\"",
      ])
      duration = "900s"
      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_SUM"
        cross_series_reducer = "REDUCE_SUM"
      }
    }
  }

  documentation {
    content = local.runbook
  }
}

resource "google_monitoring_alert_policy" "feed_failing" {
  display_name          = "train-fusion: a feed is failing"
  combiner              = "OR"
  notification_channels = [google_monitoring_notification_channel.email.id]

  conditions {
    display_name = "fewer than 10 successful fetches of a feed in 15 min (normal: ~15)"
    condition_threshold {
      filter          = "resource.type = \"global\" AND metric.type = \"${local.fetches_metric}\""
      comparison      = "COMPARISON_LT"
      threshold_value = 10
      duration        = "0s"
      aggregations {
        alignment_period     = "900s"
        per_series_aligner   = "ALIGN_DELTA"
        cross_series_reducer = "REDUCE_SUM"
        group_by_fields      = ["metric.label.feed"]
      }
      evaluation_missing_data = "EVALUATION_MISSING_DATA_ACTIVE" # no data = failing
    }
  }

  documentation {
    content = "Fetches are made but 511 isn't answering 200 (check the key, and `.http<status>` files in the archive). ${local.runbook}"
  }

  depends_on = [google_monitoring_metric_descriptor.fetches]
}

resource "google_monitoring_alert_policy" "duplicate_fetchers" {
  display_name          = "train-fusion: more than one fetcher running"
  combiner              = "OR"
  notification_channels = [google_monitoring_notification_channel.email.id]

  conditions {
    display_name = "over 22 successful fetches of a feed per 15 min, for 30 min (one fetcher: ~15)"
    condition_threshold {
      filter          = "resource.type = \"global\" AND metric.type = \"${local.fetches_metric}\""
      comparison      = "COMPARISON_GT"
      threshold_value = 22
      duration        = "1800s" # a deploy's overlap is a couple of minutes
      aggregations {
        alignment_period     = "900s"
        per_series_aligner   = "ALIGN_DELTA"
        cross_series_reducer = "REDUCE_SUM"
        group_by_fields      = ["metric.label.feed"]
      }
    }
  }

  documentation {
    content = "Is a stray fetcher VM running (`gcloud compute instances list`)? (A local `just fetch` without --metrics would not show up here.) ${local.runbook}"
  }

  depends_on = [google_monitoring_metric_descriptor.fetches]
}
