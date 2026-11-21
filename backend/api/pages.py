"""The service's web pages: universal links, the email sign-in landing page, the privacy
policy, and the health check."""
from __future__ import annotations

import html

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

from .config import Settings

APP_NAME = "Train Recorder"

STYLE = """
:root { color-scheme: light dark; }
body { font: 16px/1.5 -apple-system, system-ui, sans-serif; max-width: 42rem; margin: 2rem auto;
       padding: 0 1rem; }
h1 { font-size: 1.6rem; } h2 { font-size: 1.2rem; margin-top: 2rem; }
"""


def page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        '<meta name=viewport content="width=device-width, initial-scale=1">'
        f"<title>{html.escape(title)}</title><style>{STYLE}</style></head>"
        f"<body>{body}</body></html>")


def aasa(s: Settings) -> dict:
    return {
        "applinks": {
            "details": [{
                "appIDs": [s.apple_app_id],
                "components": [{"/": "/auth/*", "comment": "emailed sign-in links"}],
                # the older format, for iOS before 13
                "appID": s.apple_app_id,
                "paths": ["/auth/*"],
            }],
        },
    }


EMAIL_LINK = f"""
<h1>Sign in to {APP_NAME}</h1>
<p>This sign-in link has to be opened by the {APP_NAME} app.</p>
<p>Open it <strong>on the phone where you asked to sign in</strong>, with the app installed:
tap the link in the email there. If it opened this page instead, make sure the app is
installed, then long-press the link in the email and choose to open it in {APP_NAME}.</p>
<p>If you asked on another device, or the link has expired, ask for a new link in the app.</p>
"""


def privacy(s: Settings) -> str:
    if s.contact_email:
        e = html.escape(s.contact_email)
        contact = f'<a href="mailto:{e}">{e}</a>'
    else:
        contact = "the contact address in the app's App Store listing"
    return f"""
<h1>{APP_NAME}: privacy policy</h1>

<p>{APP_NAME} records audio and motion sensors on a phone during a train ride, works out
from them where the train stopped and how fast it went, and, if you choose, sends the
recording to the app's developers to improve that model. {APP_NAME} is an independent
project, not affiliated with any railway or transit agency.</p>

<h2>What stays on your phone</h2>
<p>Recordings are made and kept on your phone. Nothing is sent anywhere unless you choose
to upload a recording or sign in to sync your rides, as described below. You can use the
app without an account.</p>

<h2>What is collected, and why</h2>
<ul>
<li><strong>Recordings you choose to upload.</strong> After each ride (and from the ride
history) the app asks whether to upload that ride's recording; nothing is uploaded without
your yes. An upload is the whole audio recording, including anything said near the phone,
and the raw sensor data (motion, magnetometer, barometer) with their times. They are used
only to develop and improve the app's model of trains' stops, speed and position.</li>
<li><strong>A random device identifier.</strong> Each installation makes a random secret;
we keep only a one-way hash of it with each upload, so that you can ask for your uploads to
be deleted without an account, and link them to your account if you sign in later.</li>
<li><strong>Your account, if you sign in</strong> (with Apple, Google, or an emailed link):
an account id and your email address (with Sign in with Apple, this may be a private relay
address). They are used to sign you in, to keep your synced rides, and to link uploads to
your account.</li>
<li><strong>Ride timings, if you sign in and sync is on.</strong> For each ride: when it
started and ended, the train if you typed it in, the direction, and the stations the app
detected with the times the doors opened and closed. They are kept in your account so you
can see them later. You can turn syncing off in the app.</li>
<li><strong>Technical logs.</strong> Our hosting provider (Google Cloud) keeps request logs
(such as IP addresses and times) for operating and securing the service.</li>
</ul>

<h2>Uploads can probably be linked to you</h2>
<p>A recording can identify you even without an account: voices, and when and where you
rode. If you are signed in when you upload, the upload is linked to your account.</p>

<h2>Who sees it</h2>
<p>Uploaded recordings and synced rides are shared only with the app's developers, for
improving the model. They are stored on Google Cloud (in the United States), which
processes them on our behalf. We do not sell your data, show ads, or track you across apps
or websites, and the app contains no third-party analytics or advertising.</p>

<h2>Deleting your data</h2>
<ul>
<li><strong>Delete your account</strong> in the app's account screen. Your account and its
synced ride timings are deleted at once; recordings uploaded while signed in (or linked to
the account) are deleted within 30 days.</li>
<li><strong>Delete your synced ride timings</strong> from your account at any time in the
account screen, without deleting the account.</li>
<li><strong>Ask for your uploaded recordings to be deleted</strong> in the app's settings,
with or without an account: every recording uploaded from that phone, and every one linked
to your account, is deleted within 30 days.</li>
<li>Recordings on your phone are yours to delete; the app can also delete them
automatically after they're uploaded (a setting).</li>
</ul>
<p>Deleted files may stay in backups for up to 7 more days before they are gone for good.
If you can't use the app, write to us (below) and we'll delete your data.</p>

<h2>Children</h2>
<p>The app is not directed at children under 13, and we don't knowingly collect their
data.</p>

<h2>Changes and contact</h2>
<p>If this policy changes, the new version will be here. Questions, or requests about your
data: {contact}.</p>
"""


def add_routes(app: FastAPI, s: Settings) -> None:
    @app.get("/.well-known/apple-app-site-association", include_in_schema=False)
    def apple_app_site_association():
        return JSONResponse(aasa(s), media_type="application/json")

    @app.get("/auth/email-link", include_in_schema=False)
    def email_link():
        return page(f"Sign in to {APP_NAME}", EMAIL_LINK)

    @app.get("/privacy", include_in_schema=False)
    def privacy_page():
        return page(f"{APP_NAME}: privacy policy", privacy(s))

    # Cloud Run's front end answers some paths ending in "z" itself, /healthz among them, so
    # from outside use /health; /healthz is here for api.md and for local runs.
    @app.get("/healthz", include_in_schema=False)
    @app.get("/health", include_in_schema=False)
    def healthz():
        return PlainTextResponse("ok")
