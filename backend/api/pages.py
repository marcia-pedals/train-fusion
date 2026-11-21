"""The service's web pages: the email sign-in landing page, the privacy policy, and the
health check."""
from __future__ import annotations

import html
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse

from .config import Settings

APP_NAME = "Train Fusion"
# the app's icon (made from ios/TrainRecorder/Assets.xcassets/AppIcon), for browsers
STATIC = Path(__file__).parent / "static"
ICONS = {"favicon.ico": "image/x-icon", "icon-192.png": "image/png", "apple-touch-icon.png": "image/png"}
# the app's own URL scheme (CFBundleURLTypes in ios/project.yml)
APP_SCHEME = "trainfusion"

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
        '<link rel=icon href="/favicon.ico" sizes="any">'
        '<link rel=icon type="image/png" href="/icon-192.png">'
        '<link rel=apple-touch-icon href="/apple-touch-icon.png">'
        f"<title>{html.escape(title)}</title><style>{STYLE}</style></head>"
        f"<body>{body}</body></html>")


def email_link(code: str | None) -> str:
    """Where an emailed sign-in link ends up: Identity Platform's link goes through its own
    domain (which can't be changed) and redirects here with the one-time code, so this page
    hands the code to the app through the app's URL scheme."""
    if not code:
        return f"""
<h1>Sign in to {APP_NAME}</h1>
<p>This sign-in link isn't complete. Ask for a new one in the app.</p>
"""
    link = html.escape(f"{APP_SCHEME}://auth/email-link?" + urlencode({"mode": "signIn", "oobCode": code}))
    return f"""
<h1>Sign in to {APP_NAME}</h1>
<p><a href="{link}" style="display:inline-block; padding:.7rem 1.4rem; border-radius:.6rem;
   background:#0a66d8; color:#fff; text-decoration:none; font-weight:600">Open in {APP_NAME}</a></p>
<p>Do this <strong>on the phone where you asked to sign in</strong>, with the app installed.</p>
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
<p><em>Effective November 21, 2026.</em></p>

<p>{APP_NAME} records audio and motion sensors on a phone during a train ride, and its
localizer works out from them where the train is, where it stopped and how fast it went.
If you choose, it shares those <strong>Train Measurements</strong> for train operations
research, which is a big part of why the app exists: to learn how trains actually run. If
you choose, it also shares a ride's <strong>Full Recording</strong> with this app's
developers, to improve the localizer. {APP_NAME} is an independent project, not affiliated
with any railway or transit agency.</p>

<h2>What stays on your phone</h2>
<p>Full Recordings are made and kept on your phone. Nothing is sent anywhere unless you
choose to share Train Measurements or a Full Recording, as described below. You can use the
app without an account.</p>

<h2>What is collected, and why</h2>
<ul>
<li><strong>Train Measurements, if you choose to share them</strong> (the app asks when a
recording starts until you choose for good; it's a setting). For each ride: the train's
position, speed and stops with the exact date and time: when the ride started and ended,
the train if you typed it in, the direction, and the stations the app detected with the
times the doors opened and closed. No audio. They are used for train operations research,
by us and by others (see "Who sees collected data").</li>
<li><strong>Full Recordings you choose to share.</strong> After each ride (and from the
ride history) the app asks whether to share that ride's Full Recording; nothing is shared
without your yes. A Full Recording is all of the audio, including anything said near the
phone, and the raw sensor data (motion, compass, barometer) with their times. They are
used only to develop and improve the app's localizer.</li>
<li><strong>A random device identifier.</strong> Each installation makes a random secret;
we keep only a one-way hash of it with each Train Measurement and Full Recording you share,
so that you can delete them without an account, and link them to your account if you sign
in later.</li>
<li><strong>Your account, if you sign in</strong> (with Apple, Google, or an emailed link):
an account id and your email address (with Sign in with Apple, this may be a private relay
address). They are used to sign you in, and to link what you share (Train Measurements and
Full Recordings) to your account.</li>
<li><strong>Technical logs.</strong> Our hosting provider (Google Cloud) keeps request logs
(such as IP addresses and times) for operating and securing the service.</li>
</ul>

<h2>Who sees collected data</h2>
<p><strong>Train Measurements</strong> are shared with anyone who asks for the data. Before
we give them out, we remove your account, email address and device identifier. Those who
receive them must agree to use the data only for train operations research, and only to
publish aggregated statistics, but we cannot guarantee what they do after they receive the
data. Even without identifiers, the exact times and places of someone's regular rides
could point to them for someone who knows their routine.</p>
<p><strong>Full Recordings</strong> are shared only with this app's developers, for
improving the localizer.</p>
<p>Both are stored on Google Cloud (in the United States), which processes them on our
behalf. We do not sell your data, show ads, or track you across apps or websites, and the
app contains no third-party analytics or advertising.</p>

<h2>Deleting your data</h2>
<ul>
<li><strong>Delete your account</strong> in the app's account screen. Your account is
deleted at once; the Train Measurements and Full Recordings linked to it are deleted
within 30 days.</li>
<li><strong>Delete your shared Train Measurements</strong> in the app's settings, with or
without an account: every one shared from that phone, and every one linked to your
account, is deleted within 30 days. Copies already given to others (without your
identifiers) can't be recalled.</li>
<li><strong>Delete your shared Full Recordings</strong> in the app's settings, with or
without an account: every one shared from that phone, and every one linked to your
account, is deleted within 30 days.</li>
<li>Full Recordings on your phone are yours to delete; the app can also delete them
automatically once they're shared or you chose not to share them (a setting).</li>
</ul>
<p>Deleted data may stay in backups for up to 7 more days before it is gone for good.
If you can't use the app, write to us (below) and we'll delete your data.</p>

<h2>Children</h2>
<p>The app is not directed at children under 13, and we don't knowingly collect their
data.</p>

<h2>Do Not Track</h2>
<p>We don't track you across apps or websites, so there is nothing for a Do Not Track
signal to turn off: we treat every visitor the same whether or not one is sent.</p>

<h2>Changes and contact</h2>
<p>If this policy changes, the new version will be here, with a new effective date.
Questions, or requests about your data: {contact}.</p>
"""


def add_routes(app: FastAPI, s: Settings) -> None:
    for name, media_type in ICONS.items():
        def icon(name: str = name, media_type: str = media_type):
            return FileResponse(STATIC / name, media_type=media_type,
                                headers={"Cache-Control": "public, max-age=86400"})
        app.add_api_route(f"/{name}", icon, methods=["GET"], include_in_schema=False)

    @app.get("/auth/email-link", include_in_schema=False)
    def email_link_page(request: Request):
        return page(f"Sign in to {APP_NAME}", email_link(request.query_params.get("oobCode")))

    @app.get("/privacy", include_in_schema=False)
    def privacy_page():
        return page(f"{APP_NAME}: privacy policy", privacy(s))

    # Cloud Run's front end answers some paths ending in "z" itself, /healthz among them, so
    # from outside use /health; /healthz is here for api.md and for local runs.
    @app.get("/healthz", include_in_schema=False)
    @app.get("/health", include_in_schema=False)
    def healthz():
        return PlainTextResponse("ok")
