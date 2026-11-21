"""The real adapters for ports.py: Firestore, the recordings bucket, Identity Platform.

Credentials are the environment's (google.auth.default): on Cloud Run the service's own
service account (infra/api.tf), on a laptop your `gcloud auth application-default login`.
The google-cloud libraries are imported here, lazily, so tests never need them.
"""
from __future__ import annotations

import threading
import time
from datetime import timedelta
from typing import Any, Callable, TypeVar

from .ports import InvalidToken

T = TypeVar("T")
SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]


def _credentials(quota_project: str | None = None):
    import google.auth

    creds, _ = google.auth.default(scopes=SCOPES, quota_project_id=quota_project)
    return creds


# ------------------------------------------------------------------ Firestore

class _FirestoreTx:
    def __init__(self, client, transaction) -> None:
        self.client = client
        self.transaction = transaction

    def get(self, path: str) -> dict | None:
        snap = self.client.document(path).get(transaction=self.transaction)
        return snap.to_dict() if snap.exists else None

    def set(self, path: str, data: dict) -> None:
        self.transaction.set(self.client.document(path), data)


class FirestoreDocs:
    def __init__(self, project: str, database: str = "(default)", credentials=None) -> None:
        from google.cloud import firestore

        self._firestore = firestore
        self.client = firestore.Client(project=project, database=database, credentials=credentials)

    def get(self, path: str) -> dict | None:
        snap = self.client.document(path).get()
        return snap.to_dict() if snap.exists else None

    def set(self, path: str, data: dict) -> None:
        self.client.document(path).set(data)

    def update(self, path: str, fields: dict) -> None:
        self.client.document(path).update(fields)

    def delete(self, path: str) -> None:
        self.client.document(path).delete()

    def add(self, collection: str, data: dict) -> str:
        _, ref = self.client.collection(collection).add(data)
        return ref.id

    def query(self, collection: str, field: str | None = None, value: Any = None) -> list[tuple[str, dict]]:
        q = self.client.collection(collection)
        if field is not None:
            from google.cloud.firestore_v1.base_query import FieldFilter

            q = q.where(filter=FieldFilter(field, "==", value))
        return [(s.id, s.to_dict()) for s in q.stream()]

    def run_transaction(self, fn: Callable[[_FirestoreTx], T]) -> T:
        @self._firestore.transactional
        def run(transaction):
            return fn(_FirestoreTx(self.client, transaction))

        return run(self.client.transaction())


# ------------------------------------------------------------------ the recordings bucket

class GcsBlobs:
    """Signs URLs with IAM signBlob as the service's own account (Cloud Run has no private
    key): the account needs roles/iam.serviceAccountTokenCreator on itself."""

    def __init__(self, project: str, bucket: str, credentials=None) -> None:
        from google.cloud import storage

        self.credentials = credentials or _credentials()
        self.client = storage.Client(project=project, credentials=self.credentials)
        self.bucket = self.client.bucket(bucket)
        self._lock = threading.Lock()

    def _token(self) -> str:
        from google.auth.transport.requests import Request

        with self._lock:
            if not self.credentials.valid:
                # on Cloud Run this also fills in service_account_email (until then "default")
                self.credentials.refresh(Request())
            return self.credentials.token

    def sizes(self, prefix: str) -> dict[str, int]:
        return {b.name: b.size for b in self.client.list_blobs(self.bucket, prefix=prefix)}

    def signed_put_url(self, name: str, headers: dict[str, str], expiration: timedelta) -> str:
        token = self._token()
        return self.bucket.blob(name).generate_signed_url(
            version="v4",
            expiration=expiration,
            method="PUT",
            headers=headers,
            service_account_email=self.credentials.service_account_email,
            access_token=token,
        )


# ------------------------------------------------------------------ Identity Platform

class _CachingRequest:
    """A google.auth transport request that caches successful GETs (the token-signing
    certificates) for a while, so verifying a token doesn't fetch them every time."""

    def __init__(self, ttl_s: float = 3600) -> None:
        from google.auth.transport.requests import Request

        self._inner = Request()
        self._ttl_s = ttl_s
        self._cache: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    def __call__(self, url, method="GET", body=None, headers=None, timeout=None, **kwargs):
        if method == "GET":
            with self._lock:
                hit = self._cache.get(url)
            if hit and hit[0] > time.monotonic():
                return hit[1]
        response = self._inner(url, method=method, body=body, headers=headers, timeout=timeout, **kwargs)
        if method == "GET" and response.status == 200:
            with self._lock:
                self._cache[url] = (time.monotonic() + self._ttl_s, response)
        return response


class IdentityPlatform:
    """Token verification with google-auth; user admin with the Identity Toolkit REST API.

    quota_project: set when running with a person's credentials (the admin CLI): the
    Identity Toolkit API wants a quota project for user credentials."""

    BASE = "https://identitytoolkit.googleapis.com/v1"

    def __init__(self, project: str, credentials=None, quota_project: str | None = None) -> None:
        self.project = project
        self._credentials = credentials
        self._quota_project = quota_project
        self._session = None
        self._request = _CachingRequest()

    def verify(self, token: str) -> dict:
        from google.oauth2 import id_token

        for attempt in range(2):
            try:
                claims = id_token.verify_firebase_token(
                    token, self._request, audience=self.project, clock_skew_in_seconds=10)
                break
            except ValueError as e:  # bad signature, audience, expiry, format (MalformedError too)
                if attempt == 0 and "certificate" in str(e).lower():
                    self._request.clear()  # maybe the keys rotated since we cached them
                    continue
                raise InvalidToken(str(e)) from e
        if claims.get("iss") != f"https://securetoken.google.com/{self.project}" or not claims.get("sub"):
            raise InvalidToken("not an Identity Platform token for this project")
        return claims

    @property
    def session(self):
        if self._session is None:
            from google.auth.transport.requests import AuthorizedSession

            self._session = AuthorizedSession(self._credentials or _credentials(self._quota_project))
        return self._session

    def _accounts(self, op: str, body: dict):
        url = f"{self.BASE}/projects/{self.project}/accounts:{op}"
        return self.session.post(url, json=body, timeout=30)

    def delete_user(self, uid: str) -> None:
        r = self._accounts("delete", {"localId": uid})
        if r.status_code == 400 and "USER_NOT_FOUND" in r.text:
            return
        r.raise_for_status()

    def disable_user(self, uid: str) -> None:
        r = self._accounts("update", {"localId": uid, "disableUser": True})
        r.raise_for_status()
