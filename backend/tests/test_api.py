import json
from datetime import timedelta

from conftest import DEVICE, OTHER_DEVICE

REC = "20300101T120000Z"
REC2 = "20300101T130000Z"


def files(audio=100, meta=10):
    return [{"name": "audio_000.m4a", "size": audio, "content_type": "audio/mp4"},
            {"name": "meta.json", "size": meta, "content_type": "application/json"}]


def start(env, uid=None, device=DEVICE, rec=REC, fs=None):
    return env.client.post("/v1/uploads", headers=env.headers(uid, device),
                           json={"recording_id": rec, "files": fs if fs is not None else files()})


def put_objects(env, upload_id, fs=None):
    for f in fs if fs is not None else files():
        env.deps.blobs.objects[f"raw/{upload_id}/{f['name']}"] = f["size"]


MEASUREMENT = {
    "started_unix_s": 1763230502.12,
    "ended_unix_s": 1763233000.5,
    "app_version": "0.2 (3)",
    "train": {"trip_number": 123, "car_number": 312, "car_position": "upper"},
    "direction": "southbound",
    "station_confidence": 0.97,
    "stops": [{"station": "Example", "doors_opened_unix_s": 1763230600.0, "doors_closed_unix_s": 1763230640.0}],
}


# ------------------------------------------------------------------ auth

def test_no_token_is_401(env):
    for method, path in [("post", "/v1/claim"), ("delete", "/v1/account"), ("post", "/v1/account")]:
        r = env.client.request(method, path)
        assert r.status_code == 401
        assert r.json()["error"] == "unauthenticated"
        assert r.json()["message"]


def test_bad_token_is_401(env):
    for auth in ["Bearer nonsense", "Basic token:u1", "Bearer "]:
        r = env.client.delete("/v1/account", headers={"Authorization": auth})
        assert r.status_code == 401, auth
        assert r.json()["error"] == "unauthenticated"


def test_bad_token_on_optional_account_is_401(env):
    r = env.client.post("/v1/uploads", headers={"X-Device-Secret": DEVICE, "Authorization": "Bearer x"},
                        json={"recording_id": REC, "files": files()})
    assert r.status_code == 401


def test_not_registered_is_403(env):
    r = env.client.delete("/v1/account", headers=env.headers("u1"))
    assert r.status_code == 403
    assert r.json()["error"] == "not_registered"
    # also where the account is optional: a token that's sent must be registered
    r = start(env, uid="u1")
    assert r.status_code == 403
    assert r.json()["error"] == "not_registered"


def test_blocked_is_403(env):
    env.register("u1")
    env.docs.update("users/u1", {"blocked": True})
    r = env.client.delete("/v1/account", headers=env.headers("u1"))
    assert r.status_code == 403
    assert r.json()["error"] == "blocked"
    r = env.client.post("/v1/account", headers=env.headers("u1"))
    assert r.status_code == 403
    assert r.json()["error"] == "blocked"


def test_bad_device_secret(env):
    for secret in ["short", "x" * 129, "a" * 31, "a" * 40 + "!"]:
        r = env.client.post("/v1/uploads", headers={"X-Device-Secret": secret},
                            json={"recording_id": REC, "files": files()})
        assert r.status_code == 400, secret
        assert r.json()["error"] == "bad_device_secret"
    # required for starting an upload
    r = env.client.post("/v1/uploads", json={"recording_id": REC, "files": files()})
    assert r.status_code == 400
    assert r.json()["error"] == "bad_device_secret"
    # boundaries are fine
    for secret in ["a" * 32, "Az09_-" * 21 + "ab"]:
        assert start(env, device=secret).status_code == 201


def test_neither_device_nor_account_is_401(env):
    for method, path in [("get", "/v1/uploads"), ("post", "/v1/uploads/deletion-request")]:
        r = env.client.request(method, path)
        assert r.status_code == 401
        assert r.json()["error"] == "unauthenticated"


# ------------------------------------------------------------------ account

def test_register_is_idempotent(env):
    r = env.client.post("/v1/account", headers=env.headers("u1"))
    assert r.status_code == 201 and r.json() == {"created": True}
    r = env.client.post("/v1/account", headers=env.headers("u1"))
    assert r.status_code == 200 and r.json() == {"created": False}
    user = env.docs.get("users/u1")
    assert user["email"] == "u1@example.com" and user["blocked"] is False
    assert env.docs.get("counters/signups-2030-01-02") == {"count": 1}


def test_signup_limit(small_env):
    env = small_env
    env.register("u1")
    env.register("u2")
    r = env.client.post("/v1/account", headers=env.headers("u3"))
    assert r.status_code == 429
    body = r.json()
    assert body["error"] == "signups_full"
    assert body["retry_after_s"] == 3600  # the clock is at 23:00 UTC
    assert r.headers["Retry-After"] == "3600"
    assert env.deps.identity.deleted == ["u3"]
    assert env.docs.get("users/u3") is None
    # existing accounts still register fine
    assert env.client.post("/v1/account", headers=env.headers("u1")).json() == {"created": False}
    # the next UTC day
    env.clock.t += timedelta(hours=2)
    assert env.client.post("/v1/account", headers=env.headers("u3")).status_code == 201


def put(env, uid=None, device=DEVICE, rec=REC, body=None):
    return env.client.put(f"/v1/measurements/{rec}", headers=env.headers(uid, device),
                          json=body if body is not None else MEASUREMENT)


def test_delete_account(env):
    env.register("u1")
    env.register("u2")
    for rid in [REC, REC2]:
        assert put(env, uid="u1", rec=rid).status_code == 201
    put(env, uid="u2", device=OTHER_DEVICE)
    put(env, device=OTHER_DEVICE, rec=REC2)  # not linked: stays
    linked = start(env, uid="u1").json()["upload_id"]
    anon = start(env, rec=REC2).json()["upload_id"]  # same device, not linked: not deleted

    r = env.client.delete("/v1/account", headers=env.headers("u1"))
    assert r.status_code == 200
    assert r.json() == {"measurements_pending_deletion": 2, "uploads_pending_deletion": 1}
    assert env.docs.get("users/u1") is None
    # nothing is deleted: the account's measurements and uploads are marked, by hand later
    marked = {d["uid"]: [] for _, d in env.docs.query("measurements")}
    for i, d in env.docs.query("measurements"):
        marked[d["uid"]].append(d["deletion_requested_at"] is not None)
    assert marked == {"u1": [True, True], "u2": [False], None: [False]}
    assert env.deps.identity.deleted == ["u1"]
    assert env.docs.get(f"uploads/{linked}")["deletion_requested_at"] is not None
    assert env.docs.get(f"uploads/{anon}")["deletion_requested_at"] is None
    [(_, req)] = env.docs.query("deletion_requests")
    assert req["reason"] == "account_deleted" and req["upload_ids"] == [linked]
    assert req["measurement_ids"] == sorted(i for i, d in env.docs.query("measurements") if d["uid"] == "u1")
    assert req["uid"] == "u1" and req["done_at"] is None
    # the account is gone
    assert env.client.delete("/v1/account", headers=env.headers("u1")).json()["error"] == "not_registered"


def test_delete_account_without_uploads_makes_no_request(env):
    env.register("u1")
    r = env.client.delete("/v1/account", headers=env.headers("u1"))
    assert r.json() == {"measurements_pending_deletion": 0, "uploads_pending_deletion": 0}
    assert env.docs.query("deletion_requests") == []


# ------------------------------------------------------------------ measurements

def test_measurements_without_an_account(env):
    r = put(env)
    assert r.status_code == 201 and r.json() == {"id": REC}
    later = {**MEASUREMENT, "started_unix_s": MEASUREMENT["started_unix_s"] + 3600, "train": None,
             "ended_unix_s": None, "direction": None, "station_confidence": None,
             "stops": [{"station": None, "doors_opened_unix_s": 1.0, "doors_closed_unix_s": None}]}
    assert put(env, rec=REC2, body=later).status_code == 201

    found = env.client.get("/v1/measurements", headers=env.headers(device=DEVICE)).json()["measurements"]
    assert [x["id"] for x in found] == [REC2, REC]  # newest first
    assert {k: v for k, v in found[1].items() if k not in ("id", "linked", "shared_at_unix_s", "deletion_requested")} == MEASUREMENT
    assert found[1]["linked"] is False and found[1]["shared_at_unix_s"] == env.clock.t.timestamp()

    # replacing: 200, not counted
    assert put(env, body={**MEASUREMENT, "app_version": "0.3 (4)"}).status_code == 200
    found = env.client.get("/v1/measurements", headers=env.headers(device=DEVICE)).json()["measurements"]
    assert len(found) == 2 and found[1]["app_version"] == "0.3 (4)"
    assert env.docs.get(f"counters/anon-measurements-{env.clock.t:%Y-%m-%d}")["count"] == 2

    # the same ride id from another phone is another measurement; neither sees the other's
    assert put(env, device=OTHER_DEVICE).status_code == 201
    assert len(env.client.get("/v1/measurements", headers=env.headers(device=OTHER_DEVICE)).json()["measurements"]) == 1

    # asking for deletion marks this phone's (and records a request); nothing is deleted
    r = env.client.post("/v1/measurements/deletion-request", headers=env.headers(device=DEVICE))
    assert r.json() == {"requested": 2}
    found = env.client.get("/v1/measurements", headers=env.headers(device=DEVICE)).json()["measurements"]
    assert [m["deletion_requested"] for m in found] == [True, True]
    assert len(env.docs.query("measurements")) == 3
    [(_, req)] = env.docs.query("deletion_requests")
    assert req["reason"] == "requested" and len(req["measurement_ids"]) == 2 and req["upload_ids"] == []
    # asking again finds nothing new
    assert env.client.post("/v1/measurements/deletion-request", headers=env.headers(device=DEVICE)).json() == {"requested": 0}
    assert len(env.docs.query("deletion_requests")) == 1
    # a marked one can't be shared again (it's waiting to be deleted)
    r = put(env)
    assert r.status_code == 409 and r.json()["error"] == "deletion_pending"
    # the other phone's is untouched
    other = env.client.get("/v1/measurements", headers=env.headers(device=OTHER_DEVICE)).json()["measurements"]
    assert [m["deletion_requested"] for m in other] == [False]


def test_measurements_with_an_account(env):
    env.register("u1")
    assert put(env, uid="u1").status_code == 201
    assert put(env, uid="u1", device=OTHER_DEVICE, rec=REC2).status_code == 201
    # the account sees both, from either phone; a phone alone sees its own
    found = env.client.get("/v1/measurements", headers=env.headers("u1")).json()["measurements"]
    assert [x["id"] for x in found] == [REC2, REC] and all(x["linked"] for x in found)
    assert len(env.client.get("/v1/measurements", headers=env.headers(device=DEVICE)).json()["measurements"]) == 1
    # shared again signed out: stays linked
    assert put(env).status_code == 200
    assert env.docs.query("measurements", "device_hash", env.docs.query("measurements")[0][1]["device_hash"])[0][1]["uid"] == "u1"
    # asking signed in covers the account's and this phone's
    put(env, device=DEVICE, rec="20300101T140000Z")  # this phone, not linked
    r = env.client.post("/v1/measurements/deletion-request", headers=env.headers("u1", DEVICE))
    assert r.json() == {"requested": 3}
    assert all(d["deletion_requested_at"] is not None for _, d in env.docs.query("measurements"))


def test_measurement_limits(small_env):
    env = small_env
    assert put(env, rec=REC).status_code == 201
    assert put(env, rec=REC2).status_code == 201
    r = put(env, rec="20300101T140000Z")
    assert r.status_code == 429 and r.json()["error"] == "rate_limited" and r.json()["retry_after_s"] > 0
    assert put(env, rec=REC).status_code == 200  # updating one already there isn't refused
    env.register("u1")
    assert put(env, uid="u1", rec="20300101T140000Z").status_code == 201  # accounts count on their own
    assert put(env, uid="u1", rec="20300101T150000Z").status_code == 201
    assert put(env, uid="u1", rec="20300101T160000Z").status_code == 429
    env.clock.t += timedelta(hours=2)  # the next UTC day
    assert put(env, rec="20300101T170000Z").status_code == 201


def test_measurement_optional_fields_may_be_left_out(env):
    minimal = {"started_unix_s": 1.0, "app_version": "1", "stops": []}
    assert put(env, body=minimal).status_code == 201
    [m] = env.client.get("/v1/measurements", headers=env.headers(device=DEVICE)).json()["measurements"]
    assert m["train"] is None and m["direction"] is None


def test_bad_measurements(env):
    h = env.headers(device=DEVICE)
    bad_bodies = [
        {**MEASUREMENT, "stops": [MEASUREMENT["stops"][0]] * 201},
        {**MEASUREMENT, "train": {**MEASUREMENT["train"], "car_position": "roof"}},
        {**MEASUREMENT, "started_unix_s": "soon"},
        {**MEASUREMENT, "unknown": 1},
        {k: v for k, v in MEASUREMENT.items() if k != "started_unix_s"},
        {**MEASUREMENT, "stops": [{"station": "X"}]},
    ]
    for body in bad_bodies:
        r = put(env, body=body)
        assert r.status_code == 400, body
        assert r.json()["error"] == "bad_request"
    url = f"/v1/measurements/{REC}"
    r = env.client.put(url, headers={**h, "Content-Type": "application/json"}, content=b"{nope")
    assert r.status_code == 400
    big = json.dumps(MEASUREMENT).encode() + b" " * 64 * 1024
    r = env.client.put(url, headers={**h, "Content-Type": "application/json"}, content=big)
    assert r.status_code == 400  # over 64 KB
    assert env.client.put("/v1/measurements/not-an-id", headers=h, json=MEASUREMENT).status_code == 400
    # a device secret is needed (an account alone isn't enough to put)
    env.register("u1")
    assert env.client.put(url, headers=env.headers("u1"), json=MEASUREMENT).status_code == 400
    assert env.client.get("/v1/measurements").status_code == 401
    assert env.client.get("/v1/measurements", headers=h).json() == {"measurements": []}


# ------------------------------------------------------------------ uploads

def test_start_upload(env):
    r = start(env)
    assert r.status_code == 201
    body = r.json()
    upload_id = body["upload_id"]
    assert upload_id.startswith(REC + "-") and len(upload_id) == len(REC) + 9
    assert body["expires_unix_s"] == int((env.clock.t + timedelta(hours=12)).timestamp())
    assert [f["name"] for f in body["files"]] == ["audio_000.m4a", "meta.json"]
    f = body["files"][0]
    assert f["method"] == "PUT"
    assert f["headers"] == {"Content-Type": "audio/mp4", "x-goog-content-length-range": "100,100",
                            "x-goog-if-generation-match": "0"}
    name, headers, expiration = env.deps.blobs.signed[0]
    assert name == f"raw/{upload_id}/audio_000.m4a" and headers == f["headers"]
    assert expiration == timedelta(hours=12)
    doc = env.docs.get(f"uploads/{upload_id}")
    assert doc["uid"] is None and doc["status"] == "pending" and doc["total_bytes"] == 110
    assert len(doc["device_hash"]) == 64 and DEVICE not in str(doc)  # only the hash is kept
    assert env.docs.get("counters/anon-uploads-2030-01-02") == {"count": 1}


def test_bad_upload_requests(env):
    bad = [
        {"recording_id": "2030-01-01", "files": files()},
        {"recording_id": REC, "files": []},
        {"recording_id": REC, "files": [files()[0]]},  # no meta.json
        {"recording_id": REC, "files": files() + [files()[1]]},  # repeated name
        {"recording_id": REC, "files": [{"name": "../x", "size": 1, "content_type": "a/b"}] + files()},
        {"recording_id": REC, "files": [{"name": ".hidden", "size": 1, "content_type": "a/b"}] + files()},
        {"recording_id": REC, "files": files(audio=-1)},
        {"recording_id": REC, "files": [{"name": f"f{i}", "size": 1, "content_type": "a/b"} for i in range(50)]
         + [files()[1]]},
        {"recording_id": REC},
    ]
    for body in bad:
        r = env.client.post("/v1/uploads", headers=env.headers(device=DEVICE), json=body)
        assert r.status_code == 400, body
        assert r.json()["error"] == "bad_request"
    assert env.docs.query("uploads") == []


def test_size_limit(small_env):
    env = small_env
    r = start(env, fs=files(audio=991, meta=10))
    assert r.status_code == 413 and r.json()["error"] == "too_large"
    assert start(env, fs=files(audio=990, meta=10)).status_code == 201


def test_anonymous_limit_is_global(small_env):
    env = small_env
    assert start(env, device=DEVICE, rec=REC).status_code == 201
    assert start(env, device=OTHER_DEVICE, rec=REC2).status_code == 201
    r = start(env, device="f" * 40, rec=REC)
    assert r.status_code == 429
    assert r.json()["error"] == "rate_limited" and r.json()["retry_after_s"] == 3600
    assert len(env.docs.query("uploads")) == 2
    # signed-in uploads count separately
    env.register("u1")
    assert start(env, uid="u1", device="f" * 40, rec=REC).status_code == 201
    # a new day
    env.clock.t += timedelta(hours=1)
    assert start(env, device="f" * 40, rec="20300101T140000Z").status_code == 201


def test_per_user_limit(small_env):
    env = small_env
    env.register("u1")
    env.register("u2")
    assert start(env, uid="u1", rec=REC).status_code == 201
    assert start(env, uid="u1", rec=REC2, device=OTHER_DEVICE).status_code == 201
    r = start(env, uid="u1", rec="20300101T140000Z")
    assert r.status_code == 429 and r.json()["error"] == "rate_limited"
    assert start(env, uid="u2", rec=REC).status_code == 201
    assert env.docs.get("counters/user-uploads-u1-2030-01-02") == {"count": 2}


def test_pending_upload_is_reused_and_not_counted(small_env):
    env = small_env
    first = start(env).json()
    start(env, device=OTHER_DEVICE, rec=REC2)  # the anonymous limit (2) is now reached
    r = start(env)
    assert r.status_code == 200
    assert r.json()["upload_id"] == first["upload_id"]
    assert env.docs.get("counters/anon-uploads-2030-01-02") == {"count": 2}
    # different sizes: a different upload, which is counted (and over the limit)
    assert start(env, fs=files(audio=101)).status_code == 429
    # another device's pending upload of the same recording isn't reused
    assert start(env, device="f" * 40).status_code == 429


def test_reused_upload_gets_linked_when_signed_in(env):
    upload_id = start(env).json()["upload_id"]
    env.register("u1")
    r = start(env, uid="u1")
    assert r.status_code == 200 and r.json()["upload_id"] == upload_id
    assert env.docs.get(f"uploads/{upload_id}")["uid"] == "u1"
    assert env.docs.get("counters/user-uploads-u1-2030-01-02") is None  # not counted


def test_completed_upload_is_not_reused(env):
    upload_id = start(env).json()["upload_id"]
    put_objects(env, upload_id)
    env.client.post(f"/v1/uploads/{upload_id}/complete", headers=env.headers(device=DEVICE))
    r = start(env)
    assert r.status_code == 201 and r.json()["upload_id"] != upload_id


def test_upload_marked_for_deletion_is_not_reused(env):
    upload_id = start(env).json()["upload_id"]
    env.client.post("/v1/uploads/deletion-request", headers=env.headers(device=DEVICE))
    r = start(env)
    assert r.status_code == 201 and r.json()["upload_id"] != upload_id


def test_complete(env):
    upload_id = start(env).json()["upload_id"]
    url = f"/v1/uploads/{upload_id}/complete"
    h = env.headers(device=DEVICE)
    r = env.client.post(url, headers=h)
    assert r.status_code == 409
    assert r.json()["error"] == "incomplete" and r.json()["missing"] == ["audio_000.m4a", "meta.json"]
    env.deps.blobs.objects[f"raw/{upload_id}/audio_000.m4a"] = 100
    env.deps.blobs.objects[f"raw/{upload_id}/meta.json"] = 9  # wrong size
    r = env.client.post(url, headers=h)
    assert r.status_code == 409 and r.json()["missing"] == ["meta.json"]
    assert env.docs.get(f"uploads/{upload_id}")["status"] == "pending"
    put_objects(env, upload_id)
    r = env.client.post(url, headers=h)
    assert r.status_code == 200 and r.json() == {"status": "complete"}
    doc = env.docs.get(f"uploads/{upload_id}")
    assert doc["status"] == "complete" and doc["completed_at"] == env.clock.t
    # again: still fine, even if the objects went away
    env.deps.blobs.objects.clear()
    assert env.client.post(url, headers=h).json() == {"status": "complete"}


def test_complete_ownership(env):
    upload_id = start(env).json()["upload_id"]
    put_objects(env, upload_id)
    url = f"/v1/uploads/{upload_id}/complete"
    for h in [env.headers(device=OTHER_DEVICE)]:
        r = env.client.post(url, headers=h)
        assert r.status_code == 404 and r.json()["error"] == "not_found"
    env.register("u1")
    assert env.client.post(url, headers=env.headers("u1")).status_code == 404  # not linked to u1
    r = env.client.post("/v1/uploads/20300101T120000Z-00000000/complete", headers=env.headers(device=DEVICE))
    assert r.status_code == 404
    assert env.client.post("/v1/uploads/nonsense/complete", headers=env.headers(device=DEVICE)).status_code == 404
    # linked to u1: the account may complete it from another device
    env.client.post("/v1/claim", headers=env.headers("u1", DEVICE))
    assert env.client.post(url, headers=env.headers("u1", OTHER_DEVICE)).status_code == 200


def test_list_uploads(env):
    env.register("u1")
    a = start(env).json()["upload_id"]
    env.clock.t += timedelta(minutes=1)
    b = start(env, uid="u1", device=OTHER_DEVICE, rec=REC2).json()["upload_id"]
    env.clock.t += timedelta(minutes=1)
    c = start(env, device="f" * 40, rec=REC2).json()["upload_id"]  # someone else's

    r = env.client.get("/v1/uploads", headers=env.headers(device=DEVICE))
    assert [u["upload_id"] for u in r.json()["uploads"]] == [a]
    r = env.client.get("/v1/uploads", headers=env.headers("u1", DEVICE))
    uploads = r.json()["uploads"]
    assert [u["upload_id"] for u in uploads] == [b, a]  # newest first
    assert uploads[0] == {"upload_id": b, "recording_id": REC2, "status": "pending", "linked": True,
                          "created_unix_s": (env.clock.t - timedelta(minutes=1)).timestamp(),
                          "deletion_requested": False}
    assert uploads[1]["linked"] is False
    r = env.client.get("/v1/uploads", headers=env.headers("u1"))
    assert [u["upload_id"] for u in r.json()["uploads"]] == [b]
    assert c


def test_claim(env):
    env.register("u1")
    env.register("u2")
    a = start(env).json()["upload_id"]
    b = start(env, rec=REC2).json()["upload_id"]
    other = start(env, device=OTHER_DEVICE).json()["upload_id"]
    linked = start(env, uid="u2", rec="20300101T140000Z").json()["upload_id"]
    put(env)
    put(env, device=OTHER_DEVICE)
    r = env.client.post("/v1/claim", headers=env.headers("u1", DEVICE))
    assert r.status_code == 200 and r.json() == {"uploads": 2, "measurements": 1}
    assert env.docs.get(f"uploads/{a}")["uid"] == "u1" and env.docs.get(f"uploads/{b}")["uid"] == "u1"
    assert env.docs.get(f"uploads/{other}")["uid"] is None
    assert env.docs.get(f"uploads/{linked}")["uid"] == "u2"  # already linked: left alone
    assert sorted(str(d["uid"]) for _, d in env.docs.query("measurements")) == ["None", "u1"]
    assert env.client.post("/v1/claim", headers=env.headers("u1", DEVICE)).json() == {"uploads": 0, "measurements": 0}
    # needs both
    assert env.client.post("/v1/claim", headers=env.headers("u1")).status_code == 400
    assert env.client.post("/v1/claim", headers=env.headers(device=DEVICE)).status_code == 401


def test_deletion_request(env):
    env.register("u1")
    a = start(env).json()["upload_id"]
    b = start(env, uid="u1", device=OTHER_DEVICE, rec=REC2).json()["upload_id"]
    c = start(env, device="f" * 40, rec=REC2).json()["upload_id"]

    r = env.client.post("/v1/uploads/deletion-request", headers=env.headers("u1", DEVICE))
    assert r.status_code == 200 and r.json() == {"requested": 2}
    [(_, req)] = env.docs.query("deletion_requests")
    assert req["reason"] == "requested" and sorted(req["upload_ids"]) == sorted([a, b])
    assert req["uid"] == "u1" and len(req["device_hash"]) == 64 and req["done_at"] is None
    assert env.docs.get(f"uploads/{c}")["deletion_requested_at"] is None
    listed = env.client.get("/v1/uploads", headers=env.headers("u1", DEVICE)).json()["uploads"]
    assert all(u["deletion_requested"] for u in listed)

    # again: nothing new, no new request
    r = env.client.post("/v1/uploads/deletion-request", headers=env.headers(device=DEVICE))
    assert r.json() == {"requested": 0}
    assert len(env.docs.query("deletion_requests")) == 1
    # by device alone
    r = env.client.post("/v1/uploads/deletion-request", headers=env.headers(device="f" * 40))
    assert r.json() == {"requested": 1}


# ------------------------------------------------------------------ pages

def test_pages(env):
    r = env.client.get("/auth/email-link?apiKey=k&mode=signIn&oobCode=a%26b<c&lang=en")
    assert r.status_code == 200
    assert 'href="trainfusion://auth/email-link?mode=signIn&amp;oobCode=a%26b%3Cc"' in r.text
    r = env.client.get("/auth/email-link")
    assert r.status_code == 200 and "trainfusion://" not in r.text and "new one" in r.text
    r = env.client.get("/.well-known/apple-app-site-association")
    assert r.status_code == 404
    r = env.client.get("/privacy")
    assert r.status_code == 200 and "contact@example.com" in r.text and "30 days" in r.text
    for name, kind in [("favicon.ico", "image/x-icon"), ("icon-192.png", "image/png"), ("apple-touch-icon.png", "image/png")]:
        r = env.client.get(f"/{name}")
        assert r.status_code == 200 and r.headers["content-type"] == kind and len(r.content) > 1000
    assert '<link rel=icon href="/favicon.ico"' in env.client.get("/privacy").text
    for path in ["/healthz", "/health"]:
        r = env.client.get(path)
        assert r.status_code == 200 and r.text == "ok"
    r = env.client.get("/nowhere")
    assert r.status_code == 404 and r.json()["error"] == "not_found"
