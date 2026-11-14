# Floppy API notes

Contract check for the addon (Task 1). Sources:

- Live schema `https://floppy.asm0dey.site/api/schema/` (reachable without auth, served as `openapi-live.json`, 2026-11-14). Covers `/api/v1/*` only; `/oauth/*` is not in it.
- Floppy source (main, commit 054c66d): `src/api/fork_views_{scrobble,playback,sync,watched_state}.py`, `src/integrations/oauth_{token,device}.py`. Used for behaviour the schema does not state and for all OAuth endpoints.

**Result: every Global Constraint holds.** Corrections and additions below.

## Corrections to the plan's wording

- **Trailing slashes are required** on `/api/v1/*`: `/api/v1/scrobble/`, `/api/v1/playback/progress/`, `/api/v1/sync/bootstrap/`, `/api/v1/sync/snapshot/`, `/api/v1/sync/changes/`, `/api/v1/sync/progress-changes/`. `/oauth/*` has none: `/oauth/register`, `/oauth/device/authorization`, `/oauth/token`.
- `feeds`/`snapshots` in the bootstrap response are server-relative URLs with the query string (context, cursor, contract, resource) already filled in. Using them avoids rebuilding the query.
- **Progress feed/snapshot entries carry no timestamp** (see (a)).

## OAuth

All three endpoints accept either `application/json` or form-encoded bodies; success and errors are JSON. Errors are HTTP 400 with `{"error", "error_description"}` (rate limiting aside).

- `POST /oauth/register` `{client_name}` (required, whitespace-collapsed, max 60 chars) -> `{client_id, client_name, grant_types, token_endpoint_auth_method: "none"}`.
- `POST /oauth/device/authorization` `{client_id}` (no `scope` -> tracking preset: `scrobble:write progress:read progress:write watchlist:read watchlist:write catalog:read sync:read`) -> `{device_code, user_code, verification_uri, verification_uri_complete, expires_in, interval}`. Rate limited.
- (c) `POST /oauth/token`: **form-encoded** (the addon's choice; JSON works too). Device grant: `client_id`, `grant_type=urn:ietf:params:oauth:grant-type:device_code`, `device_code`. Refresh grant: `client_id`, `grant_type=refresh_token`, `refresh_token`. Response `{access_token, token_type: "Bearer", expires_in, refresh_token, scope}`. **Refresh rotates**: the old refresh token is revoked and a new one returned, so store both from every response. Reusing a revoked refresh token revokes the whole family.
- `POST /oauth/revoke` `{token, client_id}` (form): revokes a refresh or access token; the addon calls it on **Log out**, best effort (`src/integrations/oauth_urls.py`).
- Token errors: `authorization_pending`, `slow_down` (server also adds 5 s to its own interval; client adds 5 s too), `expired_token`, `access_denied`, `invalid_grant` (bad/used/revoked code or refresh token), `invalid_client`, `invalid_request`.

## Scrobble: `POST /api/v1/scrobble/`

Request (JSON): required `action` (`start|pause|stop|unwatch`), `media_type` (`movie|episode`), `ids` (object; at least one of `tmdb|imdb|tvdb` non-empty, else 400). Episodes: `season_number` and `episode_number` required (400 otherwise); `ids` are the **show's** ids. Optional: `title`, `series_title`, `position_seconds`, `duration_seconds`, `progress_percent` (0-100), `completed` (bool), `manual` (bool), `played_at` (datetime string), `score`.

Response 200 `{"detail": "accepted"}`. `start`/`pause` only update live "now playing". `stop` records the watch/progress. `unwatch` retracts a watch and clears the resume point; an item Floppy never tracked is a 200 no-op. Errors: 400 `{detail}`, 404 `{detail, code: "MEDIA_NOT_FOUND"}`, 503 `{detail, code: "PROVIDER_UNAVAILABLE", retryable: true}` (retry later; it is a TMDB lookup failure).

**Completion rule (`is_played`, `src/integrations/webhooks/generic_scrobble.py`):** an explicit `completed` wins; otherwise played means `position_seconds >= 0.9 * duration_seconds` (`PLAYBACK_SCROBBLE_MIN_PROGRESS_RATIO = 0.9`, `src/app/live_playback.py`). The addon uses the same 90 % rule and sends `completed` explicitly, so Kodi and Floppy agree.

(d) **`Idempotency-Key` is honoured** on this endpoint: the header (or body `client_event_id`) keys a receipt, and a repeat with the same key replays the first response instead of re-executing. The outbox should send a stable key per queued event (e.g. `kodi-<outbox id>`). A key reused with a **different** body is a permanent **409** `{"error": {"type": "conflict_error", "code": "idempotency_conflict", ...}}` (`src/integrations/delivery.py` `_settle`); retrying never helps, so the outbox drops it with a warning.

## Progress

- `PUT /api/v1/playback/progress/` `{media_type (movie|episode|podcast), ids{tmdb,imdb,tvdb,episode_uuid}, season_number, episode_number, position_seconds, duration_seconds, completed}`; required `media_type`, `ids`. 200 returns one entry (fields below). 404 when the item cannot be resolved.
- `GET /api/v1/playback/progress/` -> `{pagination, results[]}`. Query: `limit` (default 20, **max 200**), `offset`, `media_type` (comma list), `updated_since`, `completed`. Sorted by `updated_at` descending. Entry: `media_type, source, media_id, season_number, episode_number, ids, title, series_title, image, url, position_seconds, duration_seconds, completed, updated_at`.
- (a) **The only progress-update time is `updated_at` on the `/api/v1/playback/progress/` entries (ISO datetime).** `sync/snapshot?resource=progress` and `sync/progress-changes` entries have no timestamp at all: only `position_seconds, duration_seconds, completed` plus identity. For resume comparison the engine must read `updated_at` from `GET /playback/progress/` (use `updated_since` to keep it cheap) for the items it is about to apply.

## Sync

- `POST /api/v1/sync/bootstrap/` (no body) -> 200 `{contract: 2, context, generation, cursors{watched,progress}, feeds{watched,progress}, snapshots{watched,progress}}`. 409 `{code: "sync_initializing", detail}` while the server's one-time repair runs: retry later.
- `GET /api/v1/sync/snapshot/?context=&resource=watched|progress&after=&limit=` (limit 1-500, default 100) -> `{results[], has_more, after, cursor, resource}`; pass the returned `after` while `has_more`. `cursor` is the feed cursor to start from afterwards.
- `GET /api/v1/sync/changes/` and `/api/v1/sync/progress-changes/` with `contract=2&context=&cursor=&limit=` (1-500) -> `{results[], has_more, next_cursor, oldest_sequence, newest_sequence, high_water, minimum_valid_cursor, generation, recording_enabled, complete}`. Continue from `next_cursor`.
- Errors are `{code, detail}`: 409 `cursor_expired` / `cursor_invalid` / `sync_reset_required` / `sync_not_initialized` (all mean: bootstrap again, new baseline); 400 `invalid_context`, `invalid_parameter`, `invalid_page`, `unsupported_contract`.
- Entry shape (shared; fields not relevant to the resource are absent): `item_id, sequence (feeds), revision, previous_revision, media_type, media_id, source, season_number, episode_number, ids{tmdb,imdb,tvdb,...}, kind, origin_key, origin_kind, correlation_id`. Episodes carry show-level `ids` plus `season_number`/`episode_number`.
  - watched resource adds `watched` (bool), `play_count`, `watched_at` (last watched, nullable).
  - progress resource adds `position_seconds`, `duration_seconds`, `completed`.
- (b) **Yes, `watched: false` entries appear.** The watched snapshot selects every `WatchState` row for the user without filtering on `watched`, so previously watched, now unwatched items come back with `watched: false` (and `play_count` possibly 0). The watched feed likewise emits the unwatch as a row with `watched: false`. A watched snapshot is therefore not "the set of watched items": it is explicit state, and absence means "Floppy has never tracked it".
