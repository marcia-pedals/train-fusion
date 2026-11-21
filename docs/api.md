# Backend API

The recorder app's backend (`backend/`, on Cloud Run). What it's for is in
[accounts-and-uploads.md](accounts-and-uploads.md). Clients: the iOS app now; an Android
app and a web app later, so nothing here is iOS-specific.

Base URL: the Cloud Run service's URL (`just tf output api_url`). JSON in and out. Every
error is `{"error": "<code>", "message": "<a sentence a person can read>"}` with the status
below; clients show `message` and branch on `error`.

## Authentication

- **Account**: `Authorization: Bearer <Identity Platform ID token>`. Clients sign in with
  Identity Platform directly (Apple, Google, email link) and refresh the token themselves.
  The server verifies it (signature, audience = project id, expiry) and then needs the
  account to be **registered** (`POST /v1/account`) and not blocked:
  - no / bad token where one is required: `401 unauthenticated`;
  - valid token, not registered: `403 not_registered`;
  - blocked: `403 blocked`.
- **Device**: `X-Device-Secret: <random string, 32–128 chars of [A-Za-z0-9_-]>`, made once
  per install and kept secret. The server stores only its SHA-256 (hex), as `device_hash`.
  Bad format: `400 bad_device_secret`.

Day boundaries for limits are UTC midnight. `retry_after_s` in a `429` is the seconds until
the next one.

## Account

### `POST /v1/account`: register

Auth: account token (registration not needed, obviously). Idempotent.

- already registered: `200 {"created": false}`;
- new, under today's signup limit (24): stores the user, `201 {"created": true}`;
- new, over the limit: deletes the Identity Platform user that the sign-in just created,
  and `429 signups_full` with `retry_after_s`.

### `DELETE /v1/account`

Auth: account. Marks every measurement and upload linked to the account for deletion (a
deletion request, reason `account_deleted`), deletes the user record and the Identity
Platform user. Clients revoke provider tokens (Apple) before calling this.

`200 {"measurements_pending_deletion": n, "uploads_pending_deletion": m}`

## Measurements

A ride's Train Measurements. Stored per device (and linked to the account if one is
signed in when they're put); `ride_id` is the recording id (`^[0-9]{8}T[0-9]{6}Z$`).

### `PUT /v1/measurements/{ride_id}`

Auth: device secret required; account optional (links it). Creates or replaces this
device's measurements of that ride; once linked to an account it stays linked. Body (at
most 64 KB):

```json
{
  "started_unix_s": 1763230502.12,
  "ended_unix_s": 1763233000.5,
  "app_version": "0.2 (3)",
  "train": {"trip_number": 123, "car_number": 312, "car_position": "upper"},
  "direction": "southbound",
  "station_confidence": 0.97,
  "stops": [
    {"station": "Example", "doors_opened_unix_s": 1763230600.0, "doors_closed_unix_s": 1763230640.0}
  ]
}
```

`ended_unix_s`, `train`, `direction`, `station_confidence`, a stop's `station` and
`doors_closed_unix_s` may be null. `car_position`: `unspecified` / `upper` / `lower` /
`vestibule`. At most 200 stops. Unknown fields are refused.

- new: `201 {"id": "<ride_id>"}`, counted against today's limit: 10,000 without an account
  (all devices together), 1,000 per account; over it, `429 rate_limited` with
  `retry_after_s`;
- replaced: `200 {"id": "<ride_id>"}`, not counted;
- bad body or id: `400 bad_request`.

### `GET /v1/measurements`

Auth: device secret and/or account. This device's and the account's:
`200 {"measurements": [{"id": "...", ...the body as stored..., "linked": bool,
"shared_at_unix_s": ..., "deletion_requested": bool}]}`, newest first.

### `POST /v1/measurements/deletion-request`

Auth: device secret and/or account. Marks every measurement put from this device or linked
to the account, not already marked, for deletion, and records a deletion request for the
developer (`just deletions`), who deletes them by hand. `200 {"requested": n}` (0 if there
was nothing). A marked measurement is listed with `"deletion_requested": true`, and putting
it again is refused: `409 deletion_pending`.

### `POST /v1/claim`

Auth: account and device secret. Links what this device shared without an account
(uploads and measurements not linked to any account) to this one.
`200 {"uploads": n, "measurements": m}`.

## Uploads

### `POST /v1/uploads`: start (or resume) an upload

Auth: device secret required; account optional (if a token is sent it must be valid and
registered, and the upload is linked to that account).

```json
{"recording_id": "20261115T181502Z",
 "files": [{"name": "audio_000.m4a", "size": 41234567, "content_type": "audio/mp4"}, ...]}
```

Checks, in order:

1. `recording_id` matches `^[0-9]{8}T[0-9]{6}Z$`; 1–50 files; names match
   `^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$`, distinct, one of them `meta.json`; sizes ≥ 0:
   else `400 bad_request`;
2. total size ≤ 200,000,000 bytes: else `413 too_large`;
3. if this device already has an unfinished (`pending`) upload of this recording with the
   same files and sizes: reuse it, with fresh URLs, not counted again (and linked to the
   account if one is signed in now and it wasn't);
4. otherwise the limit: signed in, 12 per account per day; not signed in, 24 per day for
   all anonymous uploads together. Over: `429 rate_limited` with `retry_after_s`.

The upload id is `<recording_id>-<8 random hex>`; its files go to
`gs://<recordings bucket>/raw/<upload_id>/<name>`.

`201` (new) or `200` (reused):

```json
{"upload_id": "20261115T181502Z-3f9a0c1d",
 "expires_unix_s": 1763273702,
 "files": [{"name": "audio_000.m4a", "url": "https://storage.googleapis.com/...",
            "method": "PUT",
            "headers": {"Content-Type": "audio/mp4",
                        "x-goog-content-length-range": "41234567,41234567",
                        "x-goog-if-generation-match": "0"}}]}
```

Clients PUT each file to its URL with exactly those headers. URLs are V4 signed, valid
12 hours. A PUT answered `412` means the object is already there: count it as done.
Upload `meta.json` last.

### `POST /v1/uploads/{upload_id}/complete`

Auth: the device secret that started it, or the account it's linked to. Checks every
declared file is in the bucket at its declared size.

- all there: marks it `complete`; `200 {"status": "complete"}` (also if it already was);
- some missing or the wrong size: `409 incomplete` with `"missing": [names]`;
- not this caller's, or no such upload: `404 not_found`.

### `GET /v1/uploads`

Auth: device secret and/or account (at least one). Lists uploads made by this device or
linked to this account:

`200 {"uploads": [{"upload_id", "recording_id", "status": "pending"|"complete",
"linked": bool, "created_unix_s", "deletion_requested": bool}]}`

### `POST /v1/uploads/deletion-request`

Auth: device secret and/or account. Marks every upload made by this device or linked to
the account, not already marked, for deletion, and records a deletion request for the
developer (`just deletions`). `200 {"requested": n}` (0 if there was nothing).

## Pages

- `GET /auth/email-link`: where an emailed sign-in link lands. The link goes through
  Identity Platform's own domain (`<project>.firebaseapp.com`; this project can't change
  that), which redirects here with `oobCode`. The page's "Open in Train Fusion" button
  hands the code to the app: `trainfusion://auth/email-link?mode=signIn&oobCode=…`.
- `GET /privacy`: the privacy policy.
- `GET /healthz`: `200 ok`.

## Storage (Firestore)

| Collection | Document | Fields |
|---|---|---|
| `users` | `<uid>` | `created_at`, `email`, `blocked` |
| `measurements` | `<ride_id>-<first 16 of device_hash>` | the body, `ride_id`, `device_hash`, `uid` (or null), `shared_at` |
| `uploads` | `<upload_id>` | `recording_id`, `uid` (or null), `device_hash`, `files` [{name, size, content_type}], `total_bytes`, `status`, `created_at`, `completed_at`, `deletion_requested_at` |
| `deletion_requests` | auto id | `requested_at`, `reason` (`requested` / `account_deleted`), `uid`, `device_hash`, `upload_ids`, `measurement_ids`, `done_at` |
| `counters` | `signups-<YYYY-MM-DD>`, `anon-uploads-<day>`, `user-uploads-<uid>-<day>`, `anon-measurements-<day>`, `user-measurements-<uid>-<day>` | `count` |

Counters are incremented in a transaction with the check.
