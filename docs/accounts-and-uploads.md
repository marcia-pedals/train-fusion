# Accounts, ride sync and recording uploads: requirements

What the recorder app (`ios/`) and its backend (`backend/`) do about accounts, syncing ride
timings and uploading recordings. Self-contained: the reasoning is here alongside the
requirements. The HTTP API that implements it is in [api.md](api.md); the recorder itself is
in [recorder.md](recorder.md), deployment in [deploy.md](deploy.md).

The app is meant to go through the Apple App Store review and, later, Google Play's, so
several requirements below are there for the stores (marked *store*).

## Terms

- **Recording**: the big files of one ride (audio, raw sensors, `meta.json`): tens to
  hundreds of MB, on the phone under `Documents/recordings/<id>/`.
- **Ride**: the small summary of one ride: when it started and ended, the train (trip, car,
  position) if typed in, and the **ride timings**: the stops the model heard (station, when
  the doors opened and closed) and the direction. A ride outlives its recording on the
  phone.
- **Upload**: a recording sent to the developers' bucket to improve the model.
- **Sync**: a ride's timings sent to the rider's account.

## No credential in the app

The app holds no credential that can write to storage. Every upload goes through the
backend, which checks the limits below and hands out short-lived signed URLs, one per file,
each accepting only that file at its declared size and only creating (never replacing) an
object. (Before this, the app bundled a service-account key that could create objects in
the bucket without limit; anyone with a build could extract it.)

## Accounts

- Signing in is **optional**. Everything except syncing ride timings works without an
  account.
- Sign-in methods: **Sign in with Apple, Sign in with Google, and an emailed sign-in
  link**. The same accounts work on iOS, a future Android app, and a future web app (where
  you'll look at your data and manage your account; out of scope for now). Implemented with
  Google Cloud Identity Platform, so all three clients use the same accounts.
  (*store*: Apple requires Sign in with Apple when an app offers another social sign-in.)
- The first sign-in creates the account. At most **24 new accounts per day** (UTC day) in
  total; when that's reached, signing in as a new user says so ("too many new accounts
  today, try again tomorrow; you can still record and upload without an account") and
  leaves the person signed out.
- **Sign out** is in the account screen. Signing out keeps everything on the phone.
- **Delete account** (*store*: Apple and Google require in-app account deletion). It says
  that it deletes the account, the ride timings synced to it, and the recordings uploaded
  while signed in (or linked to it). What happens:
  - the account and its synced timings are deleted at once;
  - its uploaded recordings are marked for deletion, and the developer deletes them by
    hand (`just recording-deletions`). This is deliberate: it's a big operation that must
    reach every copy, so it's done carefully each time, not automatically. The app says
    they're deleted within 30 days;
  - for an account that signed in with Apple, the app gets a fresh Apple authorization and
    revokes the app's Apple tokens (Apple requires this).
- Blocking an abusive account: disable it in Identity Platform and mark it blocked
  (`just block-account <uid>`); the backend then refuses it.

## Ride timings sync

- When a recording stops and the rider is signed in with sync on, the ride's timings are
  synced to the account straight away (retried later if there's no network).
- Account screen:
  - **Sync ride timings to my account**: on / off (default on). Off means new rides aren't
    synced; nothing is deleted.
  - **Sync N unsynced rides**: syncs every ride on the phone that isn't in this account yet
    (e.g. rides from before signing in, or while sync was off).
  - **Delete all ride timings from my account**: deletes them from the account (not from
    the phone).
- On first sign-in on a phone with rides on it, the app offers to sync them.
- What's synced per ride: its id (the recording's start time), start and end time, app
  version, the train if typed in, the direction and its confidence, and each stop's
  station and door times (as true clock time, corrected with the recording's NTP
  measurements). These are the model's inferences at the time; the model changes.
- Syncing is idempotent: syncing a ride again replaces the earlier copy.

## Uploading recordings

### Asking after each ride

- When a recording is stopped (with the app's stop button), the app asks whether to upload
  it: **Yes / No / Don't ask again**.
- The question ("Upload this recording to help improve the model?") is short and says:
  - the recording's size;
  - **audio**: all of it, including anything said near the phone;
  - **sensor data**: motion, compass, barometer;
  - **shared only with the app's developers** (and, when signed in, linked to your
    account);
  - you can delete your uploads anytime in Settings.
- "Don't ask again" declines this one and turns off the **Ask to share with devs**
  setting (Settings). With the setting off, new recordings count as declined.
- A recording over the size limit isn't offered; History says it's too large.

### Uploading from History

- Each ride in History whose recording is still on the phone and hasn't been uploaded has
  an upload button. It shows the same explanation and asks **Yes / No** every time.

### How uploads run

- Uploads run in the background (a background URL session), so they finish even if the
  app is closed; they resume or retry when the app is next opened.
- If signed in when the upload starts, the upload is **linked to the account**, so the
  future web app can show it.
- Each phone has a random **device secret** (in the Keychain, never shown). Uploads record
  which device made them (the server keeps only a hash of the secret). This lets someone
  who isn't signed in still ask for their uploads to be deleted, and lets the app offer,
  on sign-in, to **link the recordings uploaded from this phone before signing in** to the
  account.
- The bucket path of an upload is chosen by the server: `raw/<recording id>-<random>/`, so
  two phones starting a recording in the same second can't collide.
- `meta.json` is uploaded last (as before), and the app then tells the server the upload is
  complete; the server checks every file arrived at its declared size.

### Limits (enforced by the server)

| Limit | Value |
|---|---|
| Size of one recording (all its files) | 200 MB (200,000,000 bytes) |
| Uploads without an account, all phones together | 24 per UTC day |
| Uploads per signed-in account | 12 per UTC day |
| New accounts, in total | 24 per UTC day |

An upload counts against the limit when the server hands out its URLs, not when it
finishes. Asking again for the same recording from the same phone while the earlier
attempt is unfinished reuses it and isn't counted again. When a limit is reached, the app
keeps the upload queued, shows "upload limit reached today, will retry after …", and
retries after the next UTC midnight.

There's deliberately no protection against someone using up the shared limits (no app
attestation, no per-device limits, no total byte cap): abuse is unlikely for now, and bad
accounts can be spotted and blocked by hand.

### Asking for uploads to be deleted

- Settings has **Delete shared recordings** (confirmed first): it marks every recording uploaded
  from this phone, and every one linked to the signed-in account, for deletion. As with
  account deletion, the developer deletes them by hand, within 30 days.
- Copies on developers' laptops are dealt with by occasionally deleting the local data and
  downloading again from the bucket.

## Keeping recordings on the phone

- Setting **Keep recordings on this phone**: **Don't keep** / **Keep last 3** (default) /
  **Keep all**. Rides (timings) are always kept; this is only about the big files.
- A recording is only deleted automatically when it is **settled**: uploaded, declined
  (No, or Ask turned off when it ended), or too large to upload. A recording waiting for
  its upload, being uploaded, or never asked about (e.g. from before this feature, or not
  stopped properly) is never deleted automatically, and neither is the one being replayed.
- "Keep last 3" keeps the 3 newest recordings, settled or not; older settled ones are
  deleted. "Don't keep" deletes each recording as soon as it is settled (so with Ask on,
  after the question is answered and any upload has finished).
- Changing the setting to keep fewer asks first, saying how many recordings it deletes.
- Swiping a ride away in History deletes it (ride and recording) from the phone only:
  anything synced or uploaded stays.

## App store readiness

Done in the app or the repo:

- no credential in the app (above);
- in-app account deletion, with Apple token revocation (above);
- Sign in with Apple alongside Google;
- a privacy policy, served by the backend at `/privacy` and linked from Settings;
- a privacy manifest (`PrivacyInfo.xcprivacy`): no tracking; required-reason APIs
  (UserDefaults, system boot time for the monotonic clock); collected data (audio, other
  sensor data, coarse location via the station timings, email, user id);
- explicit, specific consent before every upload (above), and permission texts that say
  recordings stay on the phone unless you choose to upload them.

To do outside the code (checklist):

- App Store Connect: privacy policy URL, App Privacy answers matching the manifest (audio
  data, coarse location, email, user id, other data; linked to the user when signed in;
  not used for tracking), support URL, age rating;
- a **demo ride for the reviewer**, who won't be on a train: a recording made for the
  purpose (it ships to everyone, so not one of the developer's own rides), bundled and
  replayable; plus review notes saying how to try it;
- don't suggest affiliation with Caltrain in the name, icon or listing;
- TestFlight external testing first (it goes through a lighter version of the review);
- for Google Play later: the Data Safety form, and a web page for account deletion
  requests.
