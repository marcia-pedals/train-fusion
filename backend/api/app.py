"""The HTTP API (docs/api.md). `create_app()` with no arguments runs against GCP; tests pass
in-memory fakes (ports.py).

Endpoints are plain `def`s (FastAPI runs them in a thread pool), since Firestore and GCS
calls block. JSON bodies are read by an async dependency with a size cap and parsed here,
so every bad body is a `400 bad_request` in the API's error format.
"""
from __future__ import annotations

import hashlib
import logging
import math
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Literal, TypeVar

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import pages
from .config import Settings
from .ports import Blobs, Docs, Identity, InvalidToken

log = logging.getLogger("api")

RECORDING_ID = r"^[0-9]{8}T[0-9]{6}Z$"
UPLOAD_ID = re.compile(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}")
FILE_NAME = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$"
CONTENT_TYPE = r"^[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]{0,63}/[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]{0,63}$"
DEVICE_SECRET = re.compile(r"[A-Za-z0-9_-]{32,128}")
URL_LIFETIME = timedelta(hours=12)
MAX_RIDE_BODY = 64 * 1024
MAX_UPLOAD_BODY = 64 * 1024


@dataclass
class Deps:
    settings: Settings
    docs: Docs
    blobs: Blobs
    identity: Identity
    now: Callable[[], datetime] = field(default=lambda: datetime.now(timezone.utc))


def real_deps(settings: Settings) -> Deps:
    from .gcp import FirestoreDocs, GcsBlobs, IdentityPlatform

    return Deps(
        settings=settings,
        docs=FirestoreDocs(settings.project),
        blobs=GcsBlobs(settings.project, settings.recordings_bucket),
        identity=IdentityPlatform(settings.project),
    )


# ------------------------------------------------------------------ errors

class ApiError(Exception):
    def __init__(self, status: int, error: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.status, self.error, self.message, self.extra = status, error, message, extra


def unauthenticated(message: str = "Sign in again.") -> ApiError:
    return ApiError(401, "unauthenticated", message)


def bad_request(message: str) -> ApiError:
    return ApiError(400, "bad_request", message)


def not_found() -> ApiError:
    return ApiError(404, "not_found", "There's no such upload.")


def error_response(status: int, error: str, message: str, **extra: Any) -> JSONResponse:
    headers = {}
    if "retry_after_s" in extra:
        headers["Retry-After"] = str(extra["retry_after_s"])
    return JSONResponse({"error": error, "message": message, **extra}, status_code=status, headers=headers)


# ------------------------------------------------------------------ bodies

class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Train(Strict):
    trip_number: int | None = None
    car_number: int | None = None
    car_position: Literal["unspecified", "upper", "lower", "vestibule"] = "unspecified"


class Stop(Strict):
    station: str | None = Field(default=None, max_length=100)
    doors_opened_unix_s: float
    doors_closed_unix_s: float | None = None


class Ride(Strict):
    started_unix_s: float
    ended_unix_s: float | None = None
    app_version: str = Field(max_length=64)
    train: Train | None = None
    direction: str | None = Field(default=None, max_length=32)
    station_confidence: float | None = None
    stops: list[Stop] = Field(max_length=200)


class FileSpec(Strict):
    name: str = Field(pattern=FILE_NAME)
    size: int = Field(ge=0)
    content_type: str = Field(pattern=CONTENT_TYPE)


class UploadStart(Strict):
    recording_id: str = Field(pattern=RECORDING_ID)
    files: list[FileSpec] = Field(min_length=1, max_length=50)


M = TypeVar("M", bound=BaseModel)


def parse(model: type[M], raw: bytes) -> M:
    try:
        return model.model_validate_json(raw)
    except ValidationError as e:
        first = e.errors()[0]
        where = ".".join(str(p) for p in first["loc"]) or "body"
        raise bad_request(f"Bad request body ({where}: {first['msg']}).") from None


def body_reader(limit: int):
    async def read(request: Request) -> bytes:
        chunks, n = [], 0
        async for chunk in request.stream():
            n += len(chunk)
            if n > limit:
                raise bad_request(f"The request body is over {limit} bytes.")
            chunks.append(chunk)
        return b"".join(chunks)

    return read


# ------------------------------------------------------------------ helpers

def unix(t: datetime | None) -> float | None:
    return t.timestamp() if t is not None else None


def day(now: datetime) -> str:
    return now.astimezone(timezone.utc).strftime("%Y-%m-%d")


def seconds_to_midnight(now: datetime) -> int:
    now = now.astimezone(timezone.utc)
    midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(1, math.ceil((midnight - now).total_seconds()))


def counted(deps: Deps, counter: str, limit: int, then: Callable[[Any], None] = lambda tx: None) -> bool:
    """Increments counters/<counter> if it's under `limit`, and does `then` in the same
    transaction. False (and nothing written) if the limit was reached."""

    def run(tx) -> bool:
        c = tx.get(f"counters/{counter}")
        n = c["count"] if c else 0
        if n >= limit:
            return False
        then(tx)
        tx.set(f"counters/{counter}", {"count": n + 1})
        return True

    return deps.docs.run_transaction(run)


# ------------------------------------------------------------------ the app

def create_app(deps: Deps | None = None) -> FastAPI:
    if deps is None:
        logging.basicConfig(level=logging.INFO)
        deps = real_deps(Settings.from_env())
    s = deps.settings
    docs = deps.docs
    app = FastAPI(title="train-fusion recorder API", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.deps = deps

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, e: ApiError):
        return error_response(e.status, e.error, e.message, **e.extra)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, e: RequestValidationError):
        return error_response(400, "bad_request", "Bad request.")

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, e: StarletteHTTPException):
        if e.status_code == 404:
            return error_response(404, "not_found", "There's nothing here.")
        if e.status_code == 405:
            return error_response(405, "method_not_allowed", "That method isn't allowed here.")
        return error_response(e.status_code, "error", str(e.detail))

    @app.exception_handler(Exception)
    async def _internal_error(request: Request, e: Exception):
        log.exception("unhandled error")
        return error_response(500, "internal", "Something went wrong on the server. Try again later.")

    # -------------------------------------------------------------- auth

    def claims_of(request: Request) -> dict | None:
        h = request.headers.get("authorization")
        if h is None:
            return None
        scheme, _, token = h.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise unauthenticated()
        try:
            return deps.identity.verify(token.strip())
        except InvalidToken:
            raise unauthenticated() from None

    def registered(claims: dict) -> str:
        uid = claims["sub"]
        user = docs.get(f"users/{uid}")
        if user is None:
            raise ApiError(403, "not_registered", "This account isn't registered. Sign in again.")
        if user.get("blocked"):
            raise ApiError(403, "blocked", "This account has been blocked.")
        return uid

    def token_claims(request: Request) -> dict:
        claims = claims_of(request)
        if claims is None:
            raise unauthenticated("Sign in first.")
        return claims

    def account(request: Request) -> str:
        return registered(token_claims(request))

    def optional_account(request: Request) -> str | None:
        claims = claims_of(request)
        return registered(claims) if claims is not None else None

    def optional_device(request: Request) -> str | None:
        secret = request.headers.get("x-device-secret")
        if secret is None:
            return None
        if not DEVICE_SECRET.fullmatch(secret):
            raise ApiError(400, "bad_device_secret", "The device secret is malformed.")
        return hashlib.sha256(secret.encode()).hexdigest()

    def device(device_hash: str | None = Depends(optional_device)) -> str:
        if device_hash is None:
            raise ApiError(400, "bad_device_secret", "The X-Device-Secret header is missing.")
        return device_hash

    def device_or_account(device_hash: str | None = Depends(optional_device),
                          uid: str | None = Depends(optional_account)) -> tuple[str | None, str | None]:
        if device_hash is None and uid is None:
            raise unauthenticated("Send a device secret or sign in.")
        return device_hash, uid

    # -------------------------------------------------------------- account

    @app.post("/v1/account")
    def register(claims: dict = Depends(token_claims)):
        uid = claims["sub"]
        now = deps.now()

        def run(tx) -> str:
            user = tx.get(f"users/{uid}")
            if user is not None:
                return "blocked" if user.get("blocked") else "exists"
            counter = f"signups-{day(now)}"
            c = tx.get(f"counters/{counter}")
            n = c["count"] if c else 0
            if n >= s.signups_per_day:
                return "full"
            tx.set(f"users/{uid}", {"created_at": now, "email": claims.get("email"), "blocked": False})
            tx.set(f"counters/{counter}", {"count": n + 1})
            return "created"

        outcome = docs.run_transaction(run)
        if outcome == "blocked":
            raise ApiError(403, "blocked", "This account has been blocked.")
        if outcome == "exists":
            return JSONResponse({"created": False}, status_code=200)
        if outcome == "created":
            log.info("registered a new account")
            return JSONResponse({"created": True}, status_code=201)
        # full: undo the sign-in, which created the Identity Platform user
        try:
            deps.identity.delete_user(uid)
        except Exception:
            log.exception("couldn't delete the identity user of a refused signup")
        raise ApiError(429, "signups_full",
                       "Too many new accounts today; try again tomorrow. "
                       "You can still record and upload without an account.",
                       retry_after_s=seconds_to_midnight(now))

    @app.delete("/v1/account")
    def delete_account(uid: str = Depends(account)):
        rides = docs.query(f"users/{uid}/rides")
        for ride_id, _ in rides:
            docs.delete(f"users/{uid}/rides/{ride_id}")
        uploads = [i for i, u in docs.query("uploads", "uid", uid) if u.get("deletion_requested_at") is None]
        request_deletion(uploads, "account_deleted", uid=uid, device_hash=None)
        deps.identity.delete_user(uid)
        docs.delete(f"users/{uid}")
        log.info("deleted an account (%d rides, %d uploads to delete)", len(rides), len(uploads))
        return {"deleted_rides": len(rides), "uploads_pending_deletion": len(uploads)}

    # -------------------------------------------------------------- rides

    def ride_path(uid: str, ride_id: str) -> str:
        if not re.fullmatch(RECORDING_ID[1:-1], ride_id):
            raise bad_request("A ride id is a recording id, like 20000101T000000Z.")
        return f"users/{uid}/rides/{ride_id}"

    @app.put("/v1/rides/{ride_id}")
    def put_ride(ride_id: str, uid: str = Depends(account), raw: bytes = Depends(body_reader(MAX_RIDE_BODY))):
        path = ride_path(uid, ride_id)
        ride = parse(Ride, raw)
        docs.set(path, {**ride.model_dump(), "synced_at": deps.now()})
        return {"id": ride_id}

    @app.get("/v1/rides")
    def list_rides(uid: str = Depends(account)):
        rides = []
        for ride_id, d in docs.query(f"users/{uid}/rides"):
            synced = d.pop("synced_at", None)
            rides.append({"id": ride_id, **d, "synced_at_unix_s": unix(synced)})
        rides.sort(key=lambda r: (r.get("started_unix_s") or 0, r["id"]), reverse=True)
        return {"rides": rides}

    @app.delete("/v1/rides")
    def delete_rides(uid: str = Depends(account)):
        rides = docs.query(f"users/{uid}/rides")
        for ride_id, _ in rides:
            docs.delete(f"users/{uid}/rides/{ride_id}")
        return {"deleted": len(rides)}

    @app.delete("/v1/rides/{ride_id}")
    def delete_ride(ride_id: str, uid: str = Depends(account)):
        path = ride_path(uid, ride_id)
        if docs.get(path) is None:
            return {"deleted": 0}
        docs.delete(path)
        return {"deleted": 1}

    # -------------------------------------------------------------- uploads

    def upload_response(upload_id: str, files: list[dict], now: datetime) -> dict:
        out = []
        for f in files:
            headers = {
                "Content-Type": f["content_type"],
                "x-goog-content-length-range": f"{f['size']},{f['size']}",
                "x-goog-if-generation-match": "0",
            }
            url = deps.blobs.signed_put_url(f"raw/{upload_id}/{f['name']}", headers, URL_LIFETIME)
            out.append({"name": f["name"], "url": url, "method": "PUT", "headers": headers})
        return {"upload_id": upload_id, "expires_unix_s": int((now + URL_LIFETIME).timestamp()), "files": out}

    def file_key(files: list[dict]) -> list[tuple]:
        return sorted((f["name"], f["size"], f["content_type"]) for f in files)

    @app.post("/v1/uploads")
    def start_upload(device_hash: str = Depends(device), uid: str | None = Depends(optional_account),
                     raw: bytes = Depends(body_reader(MAX_UPLOAD_BODY))):
        req = parse(UploadStart, raw)
        names = [f.name for f in req.files]
        if len(set(names)) != len(names):
            raise bad_request("File names must be distinct.")
        if "meta.json" not in names:
            raise bad_request("An upload must include meta.json.")
        files = [f.model_dump() for f in req.files]
        total = sum(f["size"] for f in files)
        if total > s.max_upload_bytes:
            raise ApiError(413, "too_large",
                           f"This recording is too large to upload (over {s.max_upload_bytes // 1_000_000} MB).")
        now = deps.now()

        # an unfinished attempt at the same upload from this device: reuse it, uncounted
        for upload_id, u in docs.query("uploads", "device_hash", device_hash):
            if (u["recording_id"] == req.recording_id and u["status"] == "pending"
                    and u.get("deletion_requested_at") is None
                    and file_key(u["files"]) == file_key(files)
                    and (uid is None or u.get("uid") in (None, uid))):
                if uid is not None and u.get("uid") is None:
                    docs.update(f"uploads/{upload_id}", {"uid": uid})
                return JSONResponse(upload_response(upload_id, u["files"], now), status_code=200)

        upload_id = f"{req.recording_id}-{secrets.token_hex(4)}"
        doc = {
            "recording_id": req.recording_id, "uid": uid, "device_hash": device_hash, "files": files,
            "total_bytes": total, "status": "pending", "created_at": now, "completed_at": None,
            "deletion_requested_at": None,
        }
        if uid is not None:
            counter, limit = f"user-uploads-{uid}-{day(now)}", s.user_uploads_per_day
        else:
            counter, limit = f"anon-uploads-{day(now)}", s.anon_uploads_per_day
        if not counted(deps, counter, limit, lambda tx: tx.set(f"uploads/{upload_id}", doc)):
            raise ApiError(429, "rate_limited", "Upload limit reached for today; it will be retried tomorrow.",
                           retry_after_s=seconds_to_midnight(now))
        return JSONResponse(upload_response(upload_id, files, now), status_code=201)

    def owned_upload(upload_id: str, device_hash: str | None, uid: str | None) -> dict:
        if not UPLOAD_ID.fullmatch(upload_id):
            raise not_found()
        u = docs.get(f"uploads/{upload_id}")
        if u is None or not ((device_hash is not None and u.get("device_hash") == device_hash)
                             or (uid is not None and u.get("uid") == uid)):
            raise not_found()
        return u

    @app.post("/v1/uploads/claim")
    def claim(uid: str = Depends(account), device_hash: str = Depends(device)):
        n = 0
        for upload_id, u in docs.query("uploads", "device_hash", device_hash):
            if u.get("uid") is None:
                docs.update(f"uploads/{upload_id}", {"uid": uid})
                n += 1
        return {"claimed": n}

    @app.post("/v1/uploads/deletion-request")
    def ask_deletion(who: tuple = Depends(device_or_account)):
        device_hash, uid = who
        ids = [i for i, u in mine(device_hash, uid).items() if u.get("deletion_requested_at") is None]
        request_deletion(ids, "requested", uid=uid, device_hash=device_hash)
        return {"requested": len(ids)}

    @app.post("/v1/uploads/{upload_id}/complete")
    def complete(upload_id: str, who: tuple = Depends(device_or_account)):
        u = owned_upload(upload_id, *who)
        if u["status"] == "complete":
            return {"status": "complete"}
        prefix = f"raw/{upload_id}/"
        sizes = deps.blobs.sizes(prefix)
        missing = [f["name"] for f in u["files"] if sizes.get(prefix + f["name"]) != f["size"]]
        if missing:
            raise ApiError(409, "incomplete", "Some files haven't arrived (or arrived at the wrong size).",
                           missing=missing)
        docs.update(f"uploads/{upload_id}", {"status": "complete", "completed_at": deps.now()})
        return {"status": "complete"}

    @app.get("/v1/uploads")
    def list_uploads(who: tuple = Depends(device_or_account)):
        found = mine(*who)
        out = [{
            "upload_id": i,
            "recording_id": u["recording_id"],
            "status": u["status"],
            "linked": u.get("uid") is not None,
            "created_unix_s": unix(u["created_at"]),
            "deletion_requested": u.get("deletion_requested_at") is not None,
        } for i, u in found.items()]
        out.sort(key=lambda r: (r["created_unix_s"], r["upload_id"]), reverse=True)
        return {"uploads": out}

    def mine(device_hash: str | None, uid: str | None) -> dict[str, dict]:
        found: dict[str, dict] = {}
        if device_hash is not None:
            found.update(docs.query("uploads", "device_hash", device_hash))
        if uid is not None:
            found.update(docs.query("uploads", "uid", uid))
        return found

    def request_deletion(upload_ids: list[str], reason: str, uid: str | None, device_hash: str | None) -> None:
        """Records a deletion request for the developer (`just recording-deletions`), then
        marks the uploads (in that order: a failure in between leaves the request on record)."""
        if not upload_ids:
            return
        now = deps.now()
        docs.add("deletion_requests", {
            "requested_at": now, "reason": reason, "uid": uid, "device_hash": device_hash,
            "upload_ids": sorted(upload_ids), "done_at": None,
        })
        for upload_id in upload_ids:
            docs.update(f"uploads/{upload_id}", {"deletion_requested_at": now})
        log.info("deletion request (%s) for %d uploads", reason, len(upload_ids))

    # -------------------------------------------------------------- pages

    pages.add_routes(app, s)
    return app
