# Accounts, Train Measurements and recording uploads: requirements

What the recorder app (`ios/`) and its backend (`backend/`) do about accounts, sharing
Train Measurements and uploading recordings. Self-contained: the reasoning is here alongside the
requirements. The HTTP API that implements it is in [api.md](api.md); the recorder itself is
in [recorder.md](recorder.md), deployment in [deploy.md](deploy.md).

The app is meant to go through the Apple App Store review and, later, Google Play's, so
several requirements below are there for the stores (marked *store*).

## Terms

- **Recording**: the big files of one ride (audio, raw sensors, `meta.json`): tens to
  hundreds of MB, on the phone under `Documents/recordings/<id>/`.
- **Ride**: the small summary of one ride, its **Train Measurements**: when it started and
  ended, the train (trip, car, position) if typed in, the stops the localizer heard (station,
  when the doors opened and closed) and the direction. A ride outlives its recording on the
  phone.
- **Upload**: a recording sent to the developers' bucket to improve the localizer (the
  app's model of where the train is).
- **Share** (measurements): a ride's Train Measurements sent to the developers, for train
  operations research, and given to anyone who asks for the data (below), with the
  account, email and device identifier removed first.

## No credential in the app

The app holds no credential that can write to storage. Every upload goes through the
backend, which checks the limits below and hands out short-lived signed URLs, one per file,
each accepting only that file at its declared size and only creating (never replacing) an
object. (Before this, the app bundled a service-account key that could create objects in
the bucket without limit; anyone with a build could extract it.)

## Accounts

- Signing in is **optional**. Everything works without an account; with one, what's
  shared (Train Measurements and uploads) is linked to it, to see on the web later.
- Sign-in methods: **Sign in with Apple, Sign in with Google, and an emailed sign-in
  link**. The same accounts work on iOS, a future Android app, and a future web app (where
  you'll look at your data and manage your account; out of scope for now). Implemented with
  Google Cloud Identity Platform, so all three clients use the same accounts.
  (*store*: Apple requires Sign in with Apple when an app offers another social sign-in.)
- The first sign-in creates the account. At most **24 new accounts per day** (UTC day) in
  total; when that's reached, signing in as a new user says so ("too many new accounts
  today, try again tomorrow; you can still record and upload without an account") and
  leaves the person signed out.
- The account screen is just the account (who's signed in, with what; sign out) and
  **Delete account**. Signing out keeps everything on the phone.
- On signing in, if this phone shared measurements or uploads before (with its device
  secret, not linked to any account), the app offers to link them to the account.
- **Delete account** (*store*: Apple and Google require in-app account deletion). It says
  that it deletes the account, the Train Measurements linked to it, and the recordings
  uploaded while signed in (or linked to it). What happens:
  - the account is deleted at once;
  - its Train Measurements and uploaded recordings are marked for deletion, and the
    developer deletes them by hand (`just deletions`). This is deliberate: deleting must
    reach every copy, so it's done carefully each time, not automatically. The app says
    they're deleted within 30 days;
  - for an account that signed in with Apple, the app gets a fresh Apple authorization and
    revokes the app's Apple tokens (Apple requires this).
- Blocking an abusive account: disable it in Identity Platform and mark it blocked
  (`just block-account <uid>`); the backend then refuses it.

## Sharing Train Measurements

- **Train Measurements** is the term the app uses throughout, described as "Train position,
  speed, and stops, with exact time and date". They're what's shared per ride: its id (the
  recording's start time), start and end time, app version, the train if typed in, the
  direction and its confidence, and each stop's station and door times (as true clock
  time, corrected with the recording's NTP measurements). These are the localizer's inferences
  at the time; the localizer changes. Not the audio or the sensor data (that's an upload).
- They can be shared **with or without an account**, identified like uploads by the
  device secret (below); when signed in, they're also linked to the account.
- When a recording starts, once it has started (it keeps recording meanwhile), the app asks
  "Share Train Measurements?" ("For train operations research"), one line each:
  "Position, speed, stops, times"; "No audio"; "**Anyone who asks gets it**" (with
  details behind an ⓘ: "We remove your account and device IDs first. They must agree to use the data only for train
  operations research, and only publish aggregated statistics, but we cannot guarantee
  what they do after they receive the data."); delete any time in Settings. It's asked at
  every recording until answered for good:
  - **Always share**: turns the setting below on (this ride is shared too); not asked again;
  - **Share just this ride**: shares the ride being recorded, leaves the setting off, and
    asks again next recording;
  - **No, and don't ask again**: leaves the setting off; not asked again;
  - dismissing it (swiping it down, tapping outside it) answers nothing: this ride isn't
    shared, and it's asked again next recording.
- Settings, **Train Measurements** section:
  - **Share** (with a ? that shows the question's lines, with just OK): **Ask each ride**
    (the default: the question above) / **Always share** / **Don't ask** (nothing shared). The question's answers set it the same way. With Always share, each ride's
    measurements are shared when its recording stops (retried when the app is next opened
    if there's no network). Rides recorded while it wasn't on aren't shared later.
- Settings, **Delete Shared Data** (the last row, a screen of its own so it isn't tapped
  by accident):
  - **Delete all shared Train Measurements** (confirmed first): marks every ride's measurements
    shared from this phone, and every one linked to the signed-in account, for deletion;
    the developer deletes them by hand within 30 days, as with recordings. They stay on the
    phone. A ride whose measurements are waiting to be deleted isn't shared again
    automatically; History shows it as not shared, and sharing it from there (asked first)
    undoes its deletion. Copies already given to researchers can't be recalled (they had
    identifiers removed).
- History can also **unshare** one ride's measurements (confirmed first): the same, for
  that ride only. Rides deleted from the phone whose measurements or recording are still
  shared are listed there ("Shared, not on this phone") for this.
- History can share any ride's measurements, whatever the setting: its Share button asks
  "Share Train Measurements?" for that ride (Share / Don't share), and leaves the setting
  as it is.
- Limits on new measurements (updating one already shared isn't counted): **10,000 a day**
  without an account, all devices together, and **1,000 a day** per account. Over the limit
  they stay pending and are shared another day.
- Sharing is idempotent: sharing a ride again replaces the earlier copy.

## Uploading recordings

### Asking after each ride

- When a recording is stopped (with the app's stop button), the app asks "Share full
  recording?" ("To improve the localizer", and its size): **Share** / **Don't share** / **Don't ask again**. There's deliberately no way to upload every
  recording automatically: each upload is a yes to that recording. The question says:
  - "Audio, including nearby voices";
  - "Motion, compass, barometer";
  - "Only this app's devs get it";
  - delete any time in Settings.
- "Don't ask again" declines this one and sets Settings' Full Recordings **Share** (with
  a ? like the one above) from **Ask each ride** to **Don't ask** (nothing shared). With the setting off, new recordings count as declined
  (they can still be shared from History). Nothing in the question pairs "Share" with
  remembering, so nothing can read as "share from now on".
- A recording over the size limit isn't offered; History says it's too large.

### Uploading from History

- Each ride in History whose recording is still on the phone and isn't shared has a Share
  button. It shows the same explanation and asks **Share / Don't share** every time. A
  recording whose upload is marked for deletion counts as not shared; sharing it again
  uploads it afresh (the marked upload is still deleted).

### How uploads run

- Uploads run in the background (a background URL session), so they finish even if the
  app is closed; they resume or retry when the app is next opened.
- If signed in when the upload starts, the upload is **linked to the account**, so the
  future web app can show it.
- Each phone has a random **device secret** (in the Keychain, never shown). Uploads and
  Train Measurements record which device made them (the server keeps only a hash of the
  secret). This lets someone who isn't signed in still delete what they shared, and lets
  the app offer, on sign-in, to **link what this phone shared before signing in** to the
  account.
- The bucket path of an upload is chosen by the server: `raw/<recording id>-<random>/`, so
  two phones starting a recording in the same second can't collide.
- `meta.json` is uploaded last (as before), and the app then tells the server the upload is
  complete; the server checks every file arrived at its declared size.

### Limits (enforced by the server)

| Limit | Value |
|---|---|
| Size of one recording (all its files) | 200 MB (200,000,000 bytes) |
| Uploads without an account, all phones together | 100 per UTC day |
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

- Settings, Delete Shared Data, has **Delete all shared Full Recordings** (confirmed first): it marks every recording uploaded
  from this phone, and every one linked to the signed-in account, for deletion. As with
  account deletion, the developer deletes them by hand, within 30 days.
- History can do the same for one ride's recording (**Unshare**, confirmed first), also for
  rides no longer on the phone.
- Copies on developers' laptops are dealt with by occasionally deleting the local data and
  downloading again from the bucket.

## Keeping recordings on the phone

- Setting **Keep recordings on this phone**: **Don't keep** / **Keep last 3** (default) /
  **Keep all**. Rides (their Train Measurements) are always kept; this is only about the big files.
- A recording is only deleted automatically when it is **settled**: uploaded, declined
  (No, or Ask turned off when it ended), or too large to upload. A recording waiting for
  its upload, being uploaded, or never asked about (e.g. from before this feature, or not
  stopped properly) is never deleted automatically, and neither is the one being replayed.
- "Keep last 3" keeps the 3 newest recordings, settled or not; older settled ones are
  deleted. "Don't keep" deletes each recording as soon as it is settled (so with Ask on,
  after the question is answered and any upload has finished).
- Changing the setting to keep fewer asks first, saying how many recordings it deletes.
- Deleting a ride in History (Delete Entire Trip) deletes it (ride and recording) from the
  phone only: anything shared stays shared.

## App store readiness

Done in the app or the repo:

- no credential in the app (above);
- in-app account deletion, with Apple token revocation (above);
- Sign in with Apple alongside Google;
- a privacy policy at `https://train-fusion.web.app/privacy` (Firebase Hosting passes it on
  to the backend's `/privacy`; `infra/hosting.tf`), linked from Settings;
- a privacy manifest (`PrivacyInfo.xcprivacy`): no tracking; required-reason APIs
  (UserDefaults, system boot time for the monotonic clock); collected data (audio, other
  sensor data, precise location via the Train Measurements (the train's position with exact times), email, user id);
- explicit, specific consent before every upload (above), and permission texts that say
  recordings stay on the phone unless you choose to upload them.

To do outside the code (checklist):

- App Store Connect: privacy policy URL, App Privacy answers matching the manifest (audio
  data, precise location, email, user id, other data; linked to the user when signed in;
  not used for tracking), support URL, age rating. Train Measurements are given to third
  parties who ask (researchers), which App Privacy has to say; and Google Play's Data
  Safety form counts that as "shared";
- a **demo ride for the reviewer**, who won't be on a train: done in the app, the sample
  ride (generated, not anyone's recording; [recorder.md](recorder.md), "The sample ride");
  still to do: review notes saying how to try it (History, the sample, Play back);
- don't suggest affiliation with Caltrain in the name, icon or listing;
- TestFlight external testing first (it goes through a lighter version of the review);
- for Google Play later: the Data Safety form, and a web page for account deletion
  requests.
