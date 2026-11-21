"""Settings, from environment variables (set on the Cloud Run service by infra/api.tf)."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    project: str = "train-fusion"
    recordings_bucket: str = "train-fusion-recordings"
    max_upload_bytes: int = 200_000_000
    anon_uploads_per_day: int = 24
    user_uploads_per_day: int = 12
    signups_per_day: int = 24
    # shown on /privacy; set from terraform.tfvars (gitignored), never in git
    contact_email: str = ""

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env
        d = cls()

        def get(name: str, default):
            v = env.get(name)
            if v is None or v == "":
                return default
            return type(default)(v)

        return cls(
            project=get("GCP_PROJECT", d.project),
            recordings_bucket=get("RECORDINGS_BUCKET", d.recordings_bucket),
            max_upload_bytes=get("MAX_UPLOAD_BYTES", d.max_upload_bytes),
            anon_uploads_per_day=get("ANON_UPLOADS_PER_DAY", d.anon_uploads_per_day),
            user_uploads_per_day=get("USER_UPLOADS_PER_DAY", d.user_uploads_per_day),
            signups_per_day=get("SIGNUPS_PER_DAY", d.signups_per_day),
            contact_email=get("CONTACT_EMAIL", d.contact_email),
        )
