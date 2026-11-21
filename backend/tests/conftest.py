import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # backend/, for `import api`

from fastapi.testclient import TestClient  # noqa: E402

from api.app import Deps, create_app  # noqa: E402
from api.config import Settings  # noqa: E402
from api.ports import MemoryBlobs, MemoryDocs, MemoryIdentity  # noqa: E402

DEVICE = "d" * 40
OTHER_DEVICE = "e" * 40


class Clock:
    def __init__(self) -> None:
        self.t = datetime(2030, 1, 2, 23, 0, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.t


class Env:
    def __init__(self, settings: Settings) -> None:
        self.clock = Clock()
        self.deps = Deps(settings=settings, docs=MemoryDocs(), blobs=MemoryBlobs(),
                         identity=MemoryIdentity(), now=self.clock)
        self.client = TestClient(create_app(self.deps))

    @property
    def docs(self) -> MemoryDocs:
        return self.deps.docs

    def headers(self, uid: str | None = None, device: str | None = None) -> dict:
        h = {}
        if uid is not None:
            h["Authorization"] = f"Bearer token:{uid}:{uid}@example.com"
        if device is not None:
            h["X-Device-Secret"] = device
        return h

    def register(self, uid: str) -> None:
        r = self.client.post("/v1/account", headers=self.headers(uid))
        assert r.status_code in (200, 201), r.text


@pytest.fixture
def env() -> Env:
    return Env(Settings(project="test-project", recordings_bucket="test-bucket", contact_email="contact@example.com"))


@pytest.fixture
def small_env() -> Env:
    """Small limits, to reach them quickly."""
    return Env(Settings(project="test-project", recordings_bucket="test-bucket", max_upload_bytes=1000,
                        anon_uploads_per_day=2, user_uploads_per_day=2, signups_per_day=2,
                        anon_measurements_per_day=2, user_measurements_per_day=2))
