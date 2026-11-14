# Kodi → Floppy addon — design

Date: 2026-11-14
Status: draft, awaiting approval (builder check done)

## Intent

The user is migrating from Trakt to a self-hosted Floppy instance
(`https://floppy.asm0dey.site`, source: github.com/dannyvfilms/Floppy). Past
Trakt history is already imported into Floppy. The user still runs the Trakt
Kodi addon (`script.trakt`) and wants a replacement so Trakt can be dropped.

Floppy becomes the source of truth for watched state. Hard requirement, in the
user's words: "if I delete Kodi I for sure should be able to recover state from
it" — a freshly installed Kodi must get its watched flags and resume points back
from Floppy.

The addon is organised roughly like `script.trakt`: a background service that
scrobbles, a settings screen with device-code login, context-menu actions, and a
library sync.

## Goals

1. **Scrobble** movie and episode playback to Floppy: start, pause, stop, with
   position and duration.
2. **Context menu**: mark watched / mark unwatched on library movies, episodes
   (and, for convenience, seasons and shows → applies to their episodes).
3. **Kodi → Floppy catch-up**: push Kodi's watched items (play count,
   last-played date) that Floppy doesn't have, without creating duplicates.
4. **Resume points to Floppy**: push position on pause/stop.
5. **Continuous pull (Floppy → Kodi)**: Kodi's watched flags, play counts and
   resume points follow changes made elsewhere (web UI, other devices).
6. **Restore**: on a fresh Kodi (or on demand) rebuild Kodi's watched state and
   resume points from Floppy in one pass.
7. **Watched changes made in Kodi's own UI** (toggling watched in the library,
   not via the addon menu) are pushed to Floppy.

## Non-goals

- Rating prompt / ratings (user didn't pick it).
- Music, podcasts, watchlist, collection sync, lists.
- Restoring items not in Kodi's video library (streams from other addons are
  scrobbled best-effort, but nothing is written back for them).
- Kodi versions before 21 (Omega).
- Publishing to the official Kodi addon repository (can come later).

## Anti-goals (would be failure even if it "works")

- **Mass unwatching Kodi**: an empty or wrong Floppy account, a first sync, or a
  restore must never clear watched flags in Kodi. Unwatching in Kodi happens only
  from an explicit unwatch change in Floppy's change feed after a baseline exists.
- **Duplicate plays in Floppy**: catch-up sync and replays after network
  failures must not double-count. Trakt-imported plays must not be duplicated
  by the catch-up.
- **Echo loops**: a change the addon writes into Kodi from Floppy must not be
  pushed back to Floppy as a new local change (and vice versa).
- **Blocking playback**: no network call may stall or crash Kodi's player.

## Constraints

- Kodi 21+, Python 3 addon, standard library only for HTTP (`urllib`), JSON,
  persistence (`sqlite3`). No `script.module.requests` dependency. *(my call)*
- Floppy API facts confirmed in Floppy source (`src/api/`):
  - `POST /api/v1/scrobble` — `action` ∈ start/pause/stop/unwatch;
    `media_type` movie|episode; `ids` (tmdb/imdb/tvdb, at least one);
    `season_number`/`episode_number` for episodes; `position_seconds`,
    `duration_seconds` or `progress_percent`; `played_at`, `manual`, `completed`.
    Only `stop` persists; completion = within 30 s of the end.
  - `PUT /api/v1/media/{type}/{source}/{id}/watched-state` — `{watched, play_count}`,
    `If-Match` revision, `Idempotency-Key`. A state assertion; doesn't delete
    history.
  - `GET /api/v1/sync/changes?cursor=N&connection=<key>` — ordered change feed
    for watched state (watched + play_count + watched_at + ids).
    `GET /api/v1/sync/progress-changes` — separate feed for resume points.
    Both take `contract=2` and the `context` returned by bootstrap.
    409 `cursor_expired` → re-snapshot.
  - `GET /api/v1/sync/snapshot?resource=watched|progress&context=…` — paged full
    baseline; `POST /api/v1/sync/bootstrap/` enables the durable feed.
    `/api/v1/sync/connections`, `/api/v1/sync/conflicts` exist.
  - `GET|PUT /api/v1/playback/progress` — resume points (`updated_since`).
  - `POST /api/v1/consumptions/batch` — dry-run then execute, `operation_id`.
  - Auth: OAuth 2.0 device grant with dynamic client registration
    (`src/integrations/oauth_device.py`); `TRACKING_PRESET` scopes
    (`scrobble:write progress:read progress:write watchlist:read
    watchlist:write catalog:read sync:read`) are meant for Kodi-like clients.
    Fallback: paste an API token (`X-API-Key`).
  - The interactive API docs at `/api/docs/` and `/api/schema/` on the user's
    instance are the source of truth for exact fields.
- Floppy is a fast-moving fork; Kodi-related fixes landed 2026-11-08
  (PRs #1862, #1863). Pin behaviour against the user's running version.

- **Running alongside Trakt (user's decision)**: `script.trakt` and Floppy's
  Trakt import keep running during a trial period. The addon adds no dedupe
  logic for this: Floppy's shared `app/fork_services_play_dedupe.py` treats two
  plays of one item within a runtime-sized window (15 min–3 h) as one, and both
  the Trakt importer and the scrobble/webhook path (`integrations/webhooks/base.py`)
  use it. Both scrobblers fire at playback stop, so they land well inside the
  window. Manual check during the trial: watch one movie and one episode,
  confirm one play each in Floppy after the next Trakt import.

## Approaches considered

- **A. One-way (Kodi → Floppy) Trakt-shaped addon.** Scrobble, context menu,
  catch-up push, resume push. Smallest. Rejected: can't recover a reinstalled
  Kodi.
- **A+. A plus one-off "Restore from Floppy".** Snapshot pulled on first run or
  on demand. Covers the reinstall case, about half the work of B.
- **B. A plus continuous pull** via Floppy's change feed. **Chosen by the user.**
  Kodi follows changes from other devices and the web UI; restore is just the
  snapshot step of the same mechanism.
- **C. Existing `/webhook/kodi/<token>` with a generic webhook addon.** No code,
  but no context menu, no catch-up, no pull, and issue #1118 shows event-name
  mismatches. Ruled out.

## Design (B)

### Addon shape *(my calls)*

- Addon id `script.floppy`, name "Floppy". Extension points: `xbmc.service`
  (background service), `kodi.context.item` (context menu), `xbmc.python.script`
  (entry for settings actions).
- Settings (Kodi 21 settings format): server URL (default empty, user enters
  `https://floppy.asm0dey.site`), "Log in" (device code), advanced "Use API
  token instead", toggles for scrobbling / pull sync / notify on sync, poll
  interval (default 5 min), actions "Sync now", "Restore from Floppy",
  "Push Kodi history to Floppy", "Log out".

### Components

1. **Auth** — register client once (`client_name` "Kodi on <device name>"),
   device flow: Kodi dialog shows `user_code` + verification URL (QR code if
   cheap), polls the token endpoint, stores access/refresh tokens in addon
   profile. Refresh on 401. API-token fallback.
2. **Floppy client** — thin set of functions over `urllib`: timeouts, retry
   with backoff, `Idempotency-Key` on writes. No wrapper class for testability;
   tests fake the HTTP boundary.
3. **Outbox (local queue)** — sqlite in the addon profile dir. Every outbound
   write (scrobble stop, watched-state put, progress put, batch) is enqueued
   first, then flushed. Survives offline Floppy and Kodi restarts. Start/pause
   are fire-and-forget (Now Playing card only, not queued).
4. **Player monitor** — `xbmc.Player` + `xbmc.Monitor`. On AV start resolve
   ids: library items via JSON-RPC (`VideoLibrary.GetMovieDetails` /
   `GetEpisodeDetails` + the episode's `tvshowid` → `GetTVShowDetails`
   `uniqueid`); non-library items via `InfoTagVideo.getUniqueIDs()`. Sends
   start, pause, resume (as start), stop with `position_seconds`/`duration_seconds`.
   Stop also queues a progress PUT. Kodi's own playcount bump at the end is
   recognised as originating from playback, not pushed a second time.
5. **Kodi library watcher** — `onNotification('VideoLibrary.OnUpdate')` with a
   playcount change → watched-state PUT. Suppressed while the addon itself is
   writing (echo guard: the addon records which (item, value) it just wrote and
   ignores the matching notification).
6. **Context menu** — mark watched / unwatched → Kodi JSON-RPC set playcount
   (immediate UI feedback) + watched-state PUT (via outbox). Show/season items
   expand to episodes.
7. **Sync engine** — runs at service start, every poll interval, on wake from
   sleep, and after playback stops.
   - **Baseline (first connection, restore, or `cursor_expired`)**: bootstrap
     feed if needed, page through snapshot for `watched` and `progress`, capture
     the snapshot cursor. **Merge, never replace**:
     - Floppy watched, Kodi not → set in Kodi (playcount, lastplayed).
     - Kodi watched, Floppy has **no state at all** → push to Floppy (catch-up)
       with Kodi's `lastplayed` as the play date.
     - Kodi watched, Floppy **explicitly unwatched** (state exists, watched
       false) → Floppy wins; Kodi is set unwatched. This protects Floppy from
       mass re-watching after a reinstall or an old Kodi DB restore. *(my call)*
     - Both watched → no new play. Kodi's playcount is raised to Floppy's if
       lower; Floppy's is never raised from Kodi. *(my call)*
     - Both unwatched → nothing. A baseline never unwatches an item Floppy has
       no unwatched state for.
     - Resume: take the side with the newer timestamp (Kodi has no resume
       timestamp; `lastplayed` stands in).
     - **Preview first** *(my call)*: the first baseline on a device computes
       the merge, shows counts ("push 12 to Floppy, set 1,234 watched in Kodi,
       unwatch 3"), and writes nothing until the user confirms. Later
       baselines (`cursor_expired`) apply without asking. Kodi not yet scanned
       → defer the baseline until `VideoLibrary.OnScanFinished`.
   - **Incremental**: flush the outbox first, then pull `sync/changes` from the
     stored cursor with `connection=<key>`, apply to Kodi, store cursor after
     each page is applied.
   - **Conflict rule**: an item with an unflushed local change in the outbox is
     skipped when applying pulled changes for it; the local change wins and goes
     up with `If-Match`. A 409 from Floppy re-reads the item and Floppy wins.
     *(my call)*
8. **Item matching** — Kodi → Floppy uses ids in order tmdb, imdb, tvdb
   (episodes: show ids + season/episode). Floppy → Kodi keeps an index of the
   Kodi library keyed by every unique id, rebuilt on `VideoLibrary.OnScanFinished`.
   Unmatched Floppy items are ignored silently (counted in the sync summary).

### Several Kodi devices (user's setup: several boxes, separate libraries)

- Each device registers its own OAuth client and its own sync connection
  (own cursor, own checkpoint), named "Kodi on <device name>".
- A device skips feed changes that it originated itself (matched by its
  connection/origin key or its own `Idempotency-Key`s); changes from other
  devices and the web UI are applied normally.
- Catch-up `Idempotency-Key` is derived from the item ids + Kodi `lastplayed`,
  not the device, so two boxes catching up the same viewing collapse to one
  write; Floppy's play dedupe window backs this up.
- No shared-MySQL handling (not in use).

### Play counts *(my call)*

Watched yes/no is the synced fact. Play counts travel along (Floppy → Kodi
raises Kodi's count; Kodi playback produces one play via scrobble stop) but the
addon never invents extra plays to reconcile a count difference — that is where
Trakt-imported plays would be double-counted.

### Persistence (addon profile dir)

`state.db` (sqlite): tokens, client_id, connection key, feed cursors, outbox,
last sync summary. Deleting the addon data = fresh install → baseline merge.

### Feedback

Toast notifications for login, first restore ("Restored 1,234 watched items"),
and errors that need the user (auth expired). Quiet otherwise. Log to Kodi log
with a `[Floppy]` prefix.

## Testing

- Pure logic (merge rules, conflict rule, id resolution, outbox, echo guard,
  feed paging, cursor-expired handling) unit-tested with pytest outside Kodi,
  with a fake Floppy HTTP backend and a fake Kodi JSON-RPC.
- Kodi API calls kept at the edges; manual check on a real Kodi 21 against the
  user's instance: login, scrobble a movie and an episode, mark watched/unwatched
  both ways, wipe addon data and confirm restore.
- Project conventions from the user's standing orders: `renovate.json` from the
  start, mise tasks for automation (lint, test, build addon zip).

## Left to the builder

- Exact field names and response shapes — confirm against `/api/schema/` on the
  user's instance.
- How the consumption batch's dry-run reports existing plays, and whether the
  catch-up uses batch or per-item scrobble `stop` with `played_at` + `manual`.
- Snapshot `context` parameter and connection registration details.
- Settings layout, strings, icon.
