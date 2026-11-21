"""What the service needs from GCP, as small interfaces, with in-memory fakes (for tests and
for running locally without GCP). The real adapters are in gcp.py.

- Docs: a document store with Firestore's shape. Paths are Firestore paths:
  "users/<uid>" is a document, "users/<uid>/rides" a collection.
- Blobs: the recordings bucket: object sizes under a prefix, and V4 signed PUT URLs.
- Identity: Identity Platform: verify an ID token, delete or disable a user.
"""
from __future__ import annotations

import copy
import threading
import uuid
from datetime import timedelta
from typing import Any, Callable, Protocol, TypeVar
from urllib.parse import quote

T = TypeVar("T")


class Tx(Protocol):
    """A transaction: every get before any set (as Firestore requires)."""

    def get(self, path: str) -> dict | None: ...
    def set(self, path: str, data: dict) -> None: ...


class Docs(Protocol):
    def get(self, path: str) -> dict | None: ...
    def set(self, path: str, data: dict) -> None: ...
    def update(self, path: str, fields: dict) -> None: ...  # the document must exist
    def delete(self, path: str) -> None: ...
    def add(self, collection: str, data: dict) -> str: ...  # a new document with an auto id
    # (id, data) of every document in the collection, or those whose `field` equals `value`
    def query(self, collection: str, field: str | None = None, value: Any = None) -> list[tuple[str, dict]]: ...
    # runs fn in a transaction (retried on contention) and returns what it returns
    def run_transaction(self, fn: Callable[[Tx], T]) -> T: ...


class Blobs(Protocol):
    def sizes(self, prefix: str) -> dict[str, int]: ...  # object name -> size
    # a V4 signed URL for PUT of this object, requiring exactly these headers
    def signed_put_url(self, name: str, headers: dict[str, str], expiration: timedelta) -> str: ...


class InvalidToken(Exception):
    pass


class Identity(Protocol):
    def verify(self, token: str) -> dict: ...  # the token's claims; raises InvalidToken
    def delete_user(self, uid: str) -> None: ...  # no error if there's no such user
    def disable_user(self, uid: str) -> None: ...


# ------------------------------------------------------------------ fakes

class MemoryDocs:
    def __init__(self) -> None:
        self.data: dict[str, dict] = {}
        self._lock = threading.RLock()

    def get(self, path: str) -> dict | None:
        with self._lock:
            d = self.data.get(path)
            return copy.deepcopy(d) if d is not None else None

    def set(self, path: str, data: dict) -> None:
        with self._lock:
            self.data[path] = copy.deepcopy(data)

    def update(self, path: str, fields: dict) -> None:
        with self._lock:
            if path not in self.data:
                raise KeyError(path)
            self.data[path].update(copy.deepcopy(fields))

    def delete(self, path: str) -> None:
        with self._lock:
            self.data.pop(path, None)

    def add(self, collection: str, data: dict) -> str:
        doc_id = uuid.uuid4().hex[:20]
        self.set(f"{collection}/{doc_id}", data)
        return doc_id

    def query(self, collection: str, field: str | None = None, value: Any = None) -> list[tuple[str, dict]]:
        prefix = collection + "/"
        with self._lock:
            return [
                (path[len(prefix):], copy.deepcopy(d))
                for path, d in self.data.items()
                if path.startswith(prefix) and "/" not in path[len(prefix):]
                and (field is None or d.get(field) == value)
            ]

    def run_transaction(self, fn: Callable[[Tx], T]) -> T:
        with self._lock:
            return fn(self)


class MemoryBlobs:
    def __init__(self) -> None:
        self.objects: dict[str, int] = {}  # name -> size
        self.signed: list[tuple[str, dict[str, str], timedelta]] = []

    def sizes(self, prefix: str) -> dict[str, int]:
        return {n: s for n, s in self.objects.items() if n.startswith(prefix)}

    def signed_put_url(self, name: str, headers: dict[str, str], expiration: timedelta) -> str:
        self.signed.append((name, dict(headers), expiration))
        return f"https://storage.example/{quote(name)}?signed"


class MemoryIdentity:
    """Tokens are "token:<uid>" (or "token:<uid>:<email>"); anything else is invalid."""

    def __init__(self) -> None:
        self.deleted: list[str] = []
        self.disabled: list[str] = []

    def verify(self, token: str) -> dict:
        parts = token.split(":")
        if len(parts) not in (2, 3) or parts[0] != "token" or not parts[1]:
            raise InvalidToken("bad token")
        claims = {"sub": parts[1], "user_id": parts[1]}
        if len(parts) == 3:
            claims["email"] = parts[2]
        return claims

    def delete_user(self, uid: str) -> None:
        self.deleted.append(uid)

    def disable_user(self, uid: str) -> None:
        self.disabled.append(uid)
