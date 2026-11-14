# Kodi → Floppy addon Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Kodi 21 addon `script.floppy` that scrobbles to Floppy, keeps Kodi's watched state and resume points two-way in sync with Floppy, and restores a fresh Kodi from Floppy.

**Architecture:** Pure-Python core under `resources/lib/floppy/` (HTTP, sqlite store, merge rules, sync engine) with thin Kodi edges (player monitor, library watcher, JSON-RPC, dialogs). All Floppy writes go through a persistent sqlite outbox. Pull uses Floppy's contract-2 sync API: bootstrap → snapshot baseline → `sync/changes` + `sync/progress-changes` feeds.

**Tech Stack:** Python 3.11 (Kodi 21's interpreter), stdlib only at runtime (`urllib`, `json`, `sqlite3`); uv-managed dev deps (pytest, ruff, pyright), mise tasks; GitHub Actions CI. Distributed through the existing Kodi repository `repository.splitflap` in `asm0dey/kodi-splitflap` (GitHub Pages).

**Spec:** `docs/superpowers/specs/2026-11-14-kodi-floppy-addon-design.md`

## Global Constraints

- Kodi 21 (Omega) minimum: `xbmc.python` version `3.0.1`.
- Runtime: stdlib only. No `script.module.requests` or any other addon dependency.
- Python 3.11 syntax floor (ruff `target-version = "py311"`), matching `kodi-splitflap`'s CI comment that 3.11 is Kodi 21's interpreter. CI matrix 3.11 and 3.13.
- This addon lives in its own GitHub repo (not inside `kodi-splitflap`); creating that repo and touching `kodi-splitflap` need the user's go-ahead at execution time.
- Addon id `script.floppy`, name `Floppy`, log prefix `[Floppy]`.
- OAuth: `POST {server}/oauth/register` (`client_name`), `POST {server}/oauth/device/authorization` (`client_id`, no `scope` → tracking preset), `POST {server}/oauth/token` with `grant_type=urn:ietf:params:oauth:grant-type:device_code` / `refresh_token`. Errors `authorization_pending`, `slow_down` (+5 s interval), `expired_token`, `access_denied`. API auth: `Authorization: Bearer <token>`; fallback `X-API-Key: <api token>`.
- Client name: `Kodi on <xbmc.getInfoLabel('System.FriendlyName')>`, ≤ 60 chars.
- Deviation from spec (simplification): watched/unwatched writes use scrobble `stop` (`manual`) / `unwatch` instead of the `watched-state` PUT, so episodes need no Floppy `source`/`media_id` lookup; conflicts fall back to "local pending wins, then Floppy's feed". Own-origin feed changes need no origin matching: a feed value equal to Kodi's current value is a no-op.
- Writes use only `POST /api/v1/scrobble` (start/pause/stop/unwatch, `manual`, `played_at`, `completed`) and `PUT /api/v1/playback/progress`.
- Pull: `POST /api/v1/sync/bootstrap/` → `{contract:2, context, generation, cursors{watched,progress}, feeds, snapshots}`; `GET /api/v1/sync/snapshot?context=&resource=watched|progress&after=&limit=500` → `{results, has_more, after, cursor}`; feeds `GET /api/v1/sync/changes` and `/api/v1/sync/progress-changes` with `contract=2&context=&cursor=`. 409 `{code:"cursor_expired"}` → new baseline. Entry fields: `media_type, watched, play_count, watched_at, position_seconds, duration_seconds, season_number, episode_number, ids{tmdb,imdb,tvdb}`.
- Default poll interval 5 min; also sync at service start, on wake (`onNotification` `System.OnWake`), and 30 s after playback stops.
- First baseline on a device: preview counts, write nothing until confirmed. Later baselines apply silently.
- Merge rules exactly as spec §Sync engine/Baseline and §Play counts.

## Review Focus

1. Floppy unreachable or slow during playback → player never blocks (HTTP timeout 10 s, all network off the player callback thread), writes stay queued and flush later. → Task 5 `test_stop_while_offline_is_queued_and_flushed_later`.
2. Refresh token rejected → one notification, sync stops until re-login, no retry loop. → Task 3 `test_refresh_rejected_clears_tokens_and_raises_auth_required`.
3. Item with no usable ids (home video, episode whose show has no uniqueid) → skipped and logged, never sent. → Task 4 `test_episode_without_show_ids_has_no_ref`.
4. Fresh Kodi whose library is empty / not scanned yet → baseline deferred, nothing unwatched. → Task 7 `test_baseline_deferred_when_library_empty`.
5. Floppy entry for something not in the Kodi library → counted as unmatched, ignored. → Task 6 `test_unmatched_floppy_entries_are_counted_not_applied`.

---

## File Structure

```
addon.xml                              Kodi manifest, 3 extension points
service.py                             service entry: Monitor loop, scheduling
default.py                             script entry: settings actions (login, sync now, restore, push history, logout)
context.py                             context-menu entry: mark watched / unwatched
resources/settings.xml                 Kodi 21 settings (version="1")
resources/language/resource.language.en_gb/strings.po
resources/lib/floppy/__init__.py
resources/lib/floppy/http.py           request(), FloppyError
resources/lib/floppy/store.py          Store: sqlite kv + outbox
resources/lib/floppy/auth.py           device flow + token refresh
resources/lib/floppy/refs.py           Ref model, Kodi↔Floppy id mapping
resources/lib/floppy/kodi_library.py   JSON-RPC: read library, write watched/resume
resources/lib/floppy/merge.py          pure baseline merge
resources/lib/floppy/sync.py           engine: flush outbox, baseline, feeds
resources/lib/floppy/player.py         FloppyPlayer (xbmc.Player)
resources/lib/floppy/watcher.py        library OnUpdate handling + echo guard
tests/stubs/{xbmc,xbmcaddon,xbmcgui,xbmcvfs}.py   fake Kodi modules (fail on unknown JSON-RPC methods)
tests/fake_floppy.py                   real local HTTP server faking Floppy (fails test on unexpected requests)
tests/conftest.py
mise.toml, pyproject.toml, renovate.json, .gitignore
```

---

### Task 1: Scaffold, tooling, API contract check

**Files:**
- Create: `addon.xml`, `service.py`, `default.py`, `context.py` (empty `main()`), `resources/lib/floppy/__init__.py`, `mise.toml`, `pyproject.toml`, `renovate.json`, `.gitignore`, `tests/conftest.py`, `tests/stubs/*.py`, `tests/test_manifest.py`, `docs/floppy-api-notes.md`

**Interfaces:**
- Produces: `tests/conftest.py` puts `tests/stubs` and `resources/lib` on `sys.path`; stub `xbmc.executeJSONRPC(str) -> str` dispatches to `tests.stubs.xbmc.RPC_HANDLERS: dict[str, Callable[[dict], dict]]` and raises `AssertionError` for unregistered methods; `xbmc.Monitor`, `xbmc.Player`, `xbmc.log`, `xbmc.getInfoLabel`, `xbmcaddon.Addon().getSetting/setSetting/getAddonInfo('profile')`, `xbmcgui.Dialog` (records calls), `xbmcvfs.translatePath`.

- [ ] **Step 1: Contract check against the user's instance.** Fetch `https://floppy.asm0dey.site/api/schema/` (ask the user for a token if it needs auth). Confirm the Global Constraints fields for scrobble, playback/progress, sync bootstrap/snapshot/changes/progress-changes, oauth. Record differences in `docs/floppy-api-notes.md`; if any constraint is wrong, stop and report before Task 2.
- [ ] **Step 2: Write failing test** `tests/test_manifest.py::test_manifest_declares_entry_points` — parse `addon.xml`; assert id `script.floppy`, `xbmc.python` requires version `3.0.1`, extension points `xbmc.service` (library `service.py`), `xbmc.python.script` (library `default.py`), `kodi.context.item` with menu items for `context.py` visible on `String.IsEqual(ListItem.DBType,movie) | String.IsEqual(ListItem.DBType,episode) | String.IsEqual(ListItem.DBType,season) | String.IsEqual(ListItem.DBType,tvshow)`.
- [ ] **Step 3: Run** `mise run test` → FAIL (no addon.xml).
- [ ] **Step 4: Write** `addon.xml` and the tooling: `mise.toml` (python tool; tasks `test` = `pytest`, `lint` = `ruff check .`, `zip` = build `dist/script.floppy-<version>.zip` containing only addon files, excluding tests/docs/dev config), `pyproject.toml` (pytest, ruff, pyright dev deps via uv, `uv.lock` committed; ruff py311), `.github/workflows/tests.yml` (on push/PR: `uv sync --locked`, ruff, pyright, pytest on 3.11 and 3.13, `mise run zip`, upload artifact; actions pinned by commit SHA as in `kodi-splitflap`), `renovate.json` in the precedent shape (`config:recommended`, `vulnerabilityAlerts` on, docker digests pinned, one grouped PR for non-major, majors/security separate, custom manager for the python version pin in `mise.toml`), `.gitignore` (`dist/`, `.venv/`, `__pycache__/`, `.claude/`).
- [ ] **Step 5: Run** `mise run test && mise run lint` → PASS.
- [ ] **Step 6: Commit** `chore: scaffold script.floppy addon`.

### Task 2: HTTP and local store

**Files:** Create `resources/lib/floppy/http.py`, `resources/lib/floppy/store.py`, `tests/fake_floppy.py`, `tests/test_http.py`, `tests/test_store.py`

**Interfaces:**
- Produces:
  - `class FloppyError(Exception)` with `.status: int | None` (None = network error), `.code: str | None` (from JSON `code` or OAuth `error`), `.body: dict`.
  - `request(method: str, url: str, *, token: str | None = None, api_key: str | None = None, json_body: dict | None = None, form: dict | None = None, timeout: float = 10) -> dict` — returns parsed JSON (`{}` for empty body); raises `FloppyError` on non-2xx or network failure.
  - `class Store(path: str)`: `get(key) -> str | None`, `set(key, value: str | None)`, `enqueue(kind: str, payload: dict) -> int`, `pending() -> list[tuple[int, str, dict]]` (oldest first), `done(id: int)`, `pending_refs() -> set[str]` (each payload's `ref_key`).
  - `tests/fake_floppy.py`: `FakeFloppy` context manager, real `http.server` on 127.0.0.1, `.url`, `.route(method, path, handler)`, `.requests` log; any unrouted request records a failure that `FakeFloppy.__exit__` raises.

- [ ] **Step 1: Write failing tests**
  - `test_request_sends_bearer_and_parses_json` — handler asserts `Authorization == "Bearer t"`, returns `{"ok": 1}`; result `== {"ok": 1}`.
  - `test_request_raises_with_code_on_409` — handler returns 409 `{"code":"cursor_expired"}`; `FloppyError.status == 409`, `.code == "cursor_expired"`.
  - `test_request_network_error_has_no_status` — closed port; `.status is None`.
  - `test_outbox_survives_reopen` — enqueue 2, reopen `Store` on same file, `pending()` returns both in order; `done(first)` leaves one.
  - `test_pending_refs` — enqueue payloads with `ref_key` `"movie:tmdb:603"`; `pending_refs() == {"movie:tmdb:603"}`.
- [ ] **Step 2: Run** `pytest tests/test_http.py tests/test_store.py -v` → FAIL (import errors).
- [ ] **Step 3: Implement** `request` with `urllib.request` (form → `application/x-www-form-urlencoded`), and `Store` on `sqlite3` with tables `kv(key PK, value)` and `outbox(id INTEGER PK, kind, payload JSON, created)`.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat: http client and sqlite store`.

### Task 3: Authentication (device flow)

**Files:** Create `resources/lib/floppy/auth.py`, `tests/test_auth.py`

**Interfaces:**
- Consumes: `request`, `FloppyError`, `Store`.
- Produces:
  - `class AuthRequired(Exception)`.
  - `start_login(store: Store, server: str, client_name: str) -> dict` — registers once (stores `client_id`), returns `{device_code, user_code, verification_uri, verification_uri_complete, interval, expires_in}`.
  - `poll_login(store: Store, server: str, device: dict, sleep: Callable[[float], None], cancelled: Callable[[], bool]) -> bool` — True when tokens stored; False on `expired_token`/`access_denied`/cancel.
  - `auth_headers(store: Store, server: str) -> dict` — `{"token": ...}` or `{"api_key": ...}` kwargs for `request`; refreshes when `access_expires_at` is within 60 s; raises `AuthRequired` when nothing usable.
  - `call(store, server, method, path, **kw) -> dict` — `request` with auth; on 401 refresh once and retry; on refresh failure clear tokens and raise `AuthRequired`.
  - Store keys: `client_id`, `access_token`, `refresh_token`, `access_expires_at` (epoch str), `api_key`.

- [ ] **Step 1: Write failing tests** (all against `FakeFloppy`)
  - `test_register_once` — two `start_login` calls → one `POST /oauth/register`.
  - `test_poll_handles_pending_then_slow_down_then_success` — token endpoint returns `authorization_pending`, `slow_down`, then tokens; recorded sleeps `== [5, 10]` for `interval=5`; tokens stored.
  - `test_poll_stops_on_access_denied` → False, no tokens.
  - `test_call_refreshes_on_401_and_retries` — first API call 401, refresh succeeds, retry 200.
  - `test_refresh_rejected_clears_tokens_and_raises_auth_required` — refresh returns `invalid_grant`; `AuthRequired` raised; `store.get("refresh_token") is None`.
  - `test_api_key_fallback_used_when_no_oauth` — only `api_key` set; request carries `X-API-Key`.
- [ ] **Step 2: Run** `pytest tests/test_auth.py -v` → FAIL.
- [ ] **Step 3: Implement** per Interfaces; `sleep(interval)` before each poll, interval += 5 on `slow_down`.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat: device-code login and token refresh`.

### Task 4: Item references and Kodi library access

**Files:** Create `resources/lib/floppy/refs.py`, `resources/lib/floppy/kodi_library.py`, `tests/test_refs.py`, `tests/test_kodi_library.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class Ref: media_type: str` (`"movie"|"episode"`), `ids: tuple[tuple[str, str], ...]` (sorted, only `tmdb|imdb|tvdb`, non-empty values), `season: int | None`, `episode: int | None`. Methods: `keys() -> list[str]` (one per id, e.g. `"movie:tmdb:603"`, `"episode:tvdb:81189:1:1"`), `key() -> str` (first of `keys()` in order tmdb, imdb, tvdb), `payload() -> dict` (`media_type`, `ids` dict, plus `season_number`/`episode_number` for episodes).
  - `ref_from_floppy(entry: dict) -> Ref | None`.
  - `@dataclass class KodiItem: kind: str, dbid: int, ref: Ref, playcount: int, lastplayed: str | None, resume_position: float, resume_total: float`.
  - `rpc(method: str, params: dict) -> dict` (wraps `xbmc.executeJSONRPC`, raises `RuntimeError` on `error`).
  - `load_library() -> list[KodiItem]` — `VideoLibrary.GetMovies` (`uniqueid, playcount, lastplayed, resume`), `VideoLibrary.GetTVShows` (`uniqueid`), `VideoLibrary.GetEpisodes` (`uniqueid, playcount, lastplayed, resume, season, episode, tvshowid`); episodes use their **show's** uniqueids.
  - `class LibraryIndex(items)`: `find(ref: Ref) -> KodiItem | None` (any key matches), `__len__`.
  - `item_for(kind: str, dbid: int) -> KodiItem | None` (single-item lookup via `GetMovieDetails`/`GetEpisodeDetails`).
  - `write_item(item: KodiItem, *, playcount: int | None = None, lastplayed: str | None = None, resume: tuple[float, float] | None = None)` — `VideoLibrary.SetMovieDetails`/`SetEpisodeDetails`.
  - `kodi_time_to_utc_iso(lastplayed: str) -> str` and `utc_iso_to_kodi_time(iso: str) -> str` (Kodi `YYYY-MM-DD HH:MM:SS` is local time).

- [ ] **Step 1: Write failing tests**
  - `test_movie_ref_keys_and_payload` — uniqueid `{"tmdb":"603","imdb":"tt0133093","unknown":"x"}` → keys `["movie:imdb:tt0133093","movie:tmdb:603"]` order-independent set, `key() == "movie:tmdb:603"`, payload ids drop `unknown`.
  - `test_episode_ref_uses_show_ids_and_numbers` — payload has `season_number=1, episode_number=2`, ids from the show.
  - `test_episode_without_show_ids_has_no_ref` — show uniqueid `{}` → item skipped by `load_library()`, `xbmc.log` called with `[Floppy]` warning.
  - `test_index_matches_floppy_entry_by_any_id` — Kodi movie only has imdb; Floppy entry has tmdb+imdb → found.
  - `test_write_item_sends_set_episode_details` — RPC handler asserts params `{"episodeid": 7, "playcount": 1, "lastplayed": "...", "resume": {"position": 0, "total": 0}}`.
  - `test_time_roundtrip` — with `TZ` fixed via monkeypatch, `utc_iso_to_kodi_time(kodi_time_to_utc_iso(x)) == x`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.** Page `GetEpisodes` with `limits {start,end}` of 1000.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat: item refs and Kodi library access`.

### Task 5: Scrobbling and outbox flush

**Files:** Create `resources/lib/floppy/player.py`, add `flush_outbox` to `resources/lib/floppy/sync.py`, `tests/test_player.py`, `tests/test_flush.py`

**Interfaces:**
- Consumes: `call`, `AuthRequired`, `Store`, `Ref`, `item_for`, `kodi_library.rpc`.
- Produces:
  - Outbox kinds: `"scrobble"` (payload = scrobble body + `ref_key`), `"progress"` (payload = progress body + `ref_key`). `ref_key` and optional `idempotency_key` are stripped before sending; `idempotency_key` goes out as the `Idempotency-Key` header.
  - `flush_outbox(store: Store, server: str) -> int` — sends oldest first; `done()` on 2xx and on 4xx other than 401/409/429 (logged, dropped); stops at the first network/5xx/429 error, leaving the rest; 401 → `AuthRequired` propagates.
  - `class FloppyPlayer(xbmc.Player)(store, server, on_stopped: Callable[[], None])` — `onAVStarted`, `onPlayBackPaused`, `onPlayBackResumed`, `onPlayBackStopped`, `onPlayBackEnded`. Ref from `Player.GetItem` (`id`, `type`) → `item_for`; non-library → `InfoTagVideo.getUniqueIDs()` + `getMediaType()`/season/episode/TV show ids via `getUniqueIDs` of the show if exposed, else skip. start/pause/resume → `action` start/pause/start, sent on a background thread, not queued, errors swallowed. stop/ended → enqueue `scrobble` stop (`position_seconds`, `duration_seconds`) and `progress` PUT (`position_seconds` or `null` when completed per Floppy's rule), then `on_stopped()`.
  - `player.recently_stopped(ref_key: str) -> bool` — True within 60 s of that item's stop (used by Task 8).

- [ ] **Step 1: Write failing tests**
  - `test_movie_stop_enqueues_scrobble_and_progress` — library movie at 8150/8160 s → outbox has scrobble `{"action":"stop","media_type":"movie","ids":{"tmdb":"603"},"position_seconds":8150,"duration_seconds":8160}` and progress for same ref.
  - `test_episode_scrobble_has_show_ids_and_numbers`.
  - `test_start_sent_immediately_not_queued` — `FakeFloppy` receives one scrobble start; outbox empty.
  - `test_stop_while_offline_is_queued_and_flushed_later` — server down at stop; `flush_outbox` after server starts sends both, outbox empty; stop callback returned in < 0.5 s.
  - `test_flush_drops_400_keeps_on_500` — first item 400 → dropped; second 500 → remains, flush returns 1.
  - `test_item_without_ids_is_not_scrobbled`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat: scrobbling with persistent outbox`.

### Task 6: Baseline merge (pure)

**Files:** Create `resources/lib/floppy/merge.py`, `tests/test_merge.py`

**Interfaces:**
- Consumes: `Ref`, `KodiItem`, `LibraryIndex`, `ref_from_floppy`.
- Produces:
  - `@dataclass class KodiWrite: item: KodiItem, playcount: int | None, lastplayed: str | None, resume: tuple[float, float] | None`.
  - `@dataclass class MergePlan: to_kodi: list[KodiWrite], to_floppy: list[dict]` (outbox payloads, kind `scrobble`, `action=stop, manual=True, completed=True, played_at=<utc iso of lastplayed>`, `ref_key`), `unwatch_kodi: int, watch_kodi: int, push_floppy: int, unmatched: int`; `summary() -> str` e.g. `"Push 12 to Floppy, mark 1234 watched in Kodi, unwatch 3"`.
  - `plan_baseline(library: list[KodiItem], watched: list[dict], progress: list[dict]) -> MergePlan`.

Rules (from spec, each a test):
- [ ] **Step 1: Write failing tests**
  - `test_floppy_watched_kodi_not_sets_kodi` — playcount → Floppy `play_count` (min 1), lastplayed ← `watched_at`.
  - `test_kodi_watched_floppy_has_no_state_pushes_with_lastplayed`.
  - `test_kodi_watched_floppy_explicitly_unwatched_unwatches_kodi` — entry `watched: false` present → `KodiWrite(playcount=0)`.
  - `test_both_watched_raises_kodi_count_only` — Kodi 1, Floppy 3 → Kodi 3; Kodi 3, Floppy 1 → no write, nothing to Floppy.
  - `test_both_unwatched_nothing`.
  - `test_resume_from_floppy_when_kodi_has_none` and `test_kodi_resume_kept_when_kodi_lastplayed_newer` (compare Kodi `lastplayed` with the progress entry's update time field confirmed in Task 1 notes).
  - `test_unmatched_floppy_entries_are_counted_not_applied`.
  - `test_summary_text`.
  - `test_catch_up_idempotency_key_is_device_independent` — same item + lastplayed in two plans → same key.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** `plan_baseline`.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat: baseline merge rules`.

### Task 7: Sync engine (bootstrap, baseline, feeds)

**Files:** Modify `resources/lib/floppy/sync.py`; create `tests/test_sync.py`

**Interfaces:**
- Consumes: `call`, `AuthRequired`, `FloppyError`, `Store`, `flush_outbox`, `load_library`, `LibraryIndex`, `write_item`, `plan_baseline`, `MergePlan`, `ref_from_floppy`, echo guard `watcher.suppress(ref_key)` (Task 8 produces; here pass `suppress: Callable[[str], None]`).
- Produces:
  - Store keys: `sync_context`, `cursor_watched`, `cursor_progress`, `baseline_done` (`"1"`), `last_summary`.
  - `run_sync(store, server, *, confirm: Callable[[MergePlan], bool], suppress: Callable[[str], None], force_baseline: bool = False) -> str` — returns a status string: `"deferred"`, `"cancelled"`, `"baseline"`, `"incremental"`.
  - Order: `flush_outbox` → if no `sync_context` or `force_baseline` → baseline; else incremental; a 409 `cursor_expired` or `sync_not_initialized` during incremental → baseline.
  - Baseline: `POST sync/bootstrap/`, page both snapshots (`limit=500`) to completion, `plan_baseline`. If library empty → `"deferred"`. If `baseline_done` unset (first on device) or `force_baseline` from the Restore action → `confirm(plan)`; False → `"cancelled"`, store nothing. Apply: Kodi writes (calling `suppress(ref_key)` before each), enqueue `to_floppy`, store context + each snapshot's `cursor`, set `baseline_done`.
  - Incremental: for each feed, page from stored cursor; per entry skip if `ref.key()` in `store.pending_refs()` (local wins); else apply to the matched Kodi item if its value differs (watched → playcount/lastplayed; unwatched → playcount 0; progress → resume); store `next_cursor` after each page.

- [ ] **Step 1: Write failing tests** (FakeFloppy + stub RPC library)
  - `test_first_baseline_asks_and_cancel_writes_nothing` — `confirm` returns False → no `Set*Details` RPC, no outbox, no `sync_context`.
  - `test_first_baseline_applies_after_confirm` — Kodi writes and outbox entries match the plan; cursors stored from snapshot `cursor`.
  - `test_baseline_deferred_when_library_empty` — no confirm call, no writes, returns `"deferred"`.
  - `test_snapshot_pages_followed_via_after` — two pages, `has_more` true then false.
  - `test_incremental_applies_unwatch_from_feed` — feed entry `watched:false` for a watched Kodi episode → playcount 0, `suppress` called with its key.
  - `test_incremental_skips_item_with_pending_local_change`.
  - `test_cursor_expired_triggers_silent_rebaseline` — `baseline_done` set → no `confirm` call.
  - `test_outbox_flushed_before_pull` — request log order: scrobble before `sync/changes`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat: two-way sync engine`.

### Task 8: Library watcher, echo guard, context menu

**Files:** Create `resources/lib/floppy/watcher.py`, implement `context.py`; create `tests/test_watcher.py`, `tests/test_context.py`

**Interfaces:**
- Consumes: `Store`, `item_for`, `write_item`, `kodi_library.rpc`, `FloppyPlayer.recently_stopped`.
- Produces:
  - `suppress(ref_key: str)` / `is_suppressed(ref_key) -> bool` — module-level, entries expire after 30 s; consumed on first match.
  - `mark(store: Store, item: KodiItem, watched: bool)` — enqueue scrobble `stop` with `manual=True, completed=True, played_at=now` or `unwatch`; payload carries `ref_key`.
  - `handle_notification(store, method: str, data: str, recently_stopped: Callable[[str], bool]) -> None` — on `VideoLibrary.OnUpdate` with `item.type` movie/episode and `playcount` in data: load item; if `is_suppressed` or `recently_stopped` → ignore; else `mark(item, playcount > 0)`.
  - `context.py main()` — reads `ListItem.DBType`/`ListItem.DBID`; movie/episode → toggle based on current playcount (label "Mark watched in Floppy"/"Mark unwatched in Floppy" chosen by `ListItem.PlayCount`); season/tvshow → all its episodes via `GetEpisodes` filter. Each: `suppress(key)`, `write_item(playcount)`, `mark(...)`. Opens its own `Store` on the profile path; does not flush (service flushes within 5 s via a `NotifyAll` `floppy.flush` notification).

- [ ] **Step 1: Write failing tests**
  - `test_kodi_ui_toggle_pushes_watched`.
  - `test_update_from_sync_is_not_echoed` — `suppress(key)` then notification → outbox empty.
  - `test_playback_end_playcount_not_double_pushed` — `recently_stopped` True → outbox empty.
  - `test_context_mark_season_marks_all_episodes` — 3 episodes → 3 RPC writes, 3 outbox entries, `JSONRPC.NotifyAll` sent with `message: "floppy.flush"`.
  - `test_context_unwatch_sends_unwatch_action`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat: library watcher and context menu`.

### Task 9: Service loop, settings actions, packaging

**Files:** Implement `service.py`, `default.py`; create `resources/settings.xml`, `resources/language/resource.language.en_gb/strings.po`, `tests/test_service.py`, `tests/test_default.py`, `docs/manual-test.md`

**Interfaces:**
- Consumes: everything above.
- Produces:
  - `service.py`: `class Service(xbmc.Monitor)` with `tick(now: float)` deciding `run_sync` when `now - last_sync >= interval*60`, or a pending trigger (start, `System.OnWake`, 30 s after `on_stopped`, `Other.floppy.flush` → flush only). `confirm` = `xbmcgui.Dialog().yesno` with `plan.summary()`. `AuthRequired` → one notification ("Floppy: please log in again"), set `auth_needed` so ticks skip until login. Loop: `while not abortRequested(): tick(); waitForAbort(5)`.
  - Settings (ids): `server_url` (text), `login` (action → `RunScript(script.floppy,login)`), `api_key` (hidden text, advanced), `scrobble_enabled` (bool, true), `sync_enabled` (bool, true), `poll_minutes` (integer 1–60, 5), `notify_sync` (bool, false), actions `sync_now`, `restore`, `push_history`, `logout`.
  - `default.py main(argv)`: `login` → `start_login`, `DialogProgress` showing `user_code` and `verification_uri`, `poll_login` with `cancelled=progress.iscanceled`; `sync_now` → `NotifyAll floppy.sync`; `restore` and `push_history` → `run_sync(force_baseline=True)` with confirm (push_history is the same baseline — catch-up is part of the merge); `logout` → `POST oauth/revoke` best-effort, clear tokens, `sync_context`, cursors, `baseline_done`.

- [ ] **Step 1: Write failing tests**
  - `test_tick_runs_sync_after_interval_not_before`.
  - `test_wake_triggers_sync`.
  - `test_auth_required_notifies_once_and_pauses` — two ticks → one notification, one sync attempt.
  - `test_login_shows_code_and_stores_tokens` — dialog text contains `user_code`.
  - `test_logout_clears_sync_state`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** service, default, settings.xml, strings.po (every label referenced by id).
- [ ] **Step 4: Run** `mise run test && mise run lint && mise run zip` → PASS; zip contains `script.floppy/addon.xml` and no `tests/`.
- [ ] **Step 5: Write `docs/manual-test.md`** — on a real Kodi 21 against `https://floppy.asm0dey.site`: install zip; log in by code; first-sync preview shown and counts plausible; scrobble a movie and an episode (Now Playing card, one play each after the next Trakt import); mark watched/unwatched from context menu and Kodi UI, check Floppy; unwatch in Floppy web, Kodi follows within 5 min; delete addon data, reinstall, restore brings state back; second Kodi box sees first box's change.
- [ ] **Step 6: Commit** `feat: service loop, settings and packaging`.

### Task 10: Release and publish through the split-flap Kodi repository

Mechanism *(my call)*: **pull, not push**. This repo publishes a zip as a GitHub Release asset; `kodi-splitflap`'s `tools/build_repo.py` learns to include external addons listed in a manifest. No cross-repo tokens.

**Files (this repo):** Create `.github/workflows/release.yml`, `tools/check_version.py`, `tests/test_check_version.py`
**Files (`asm0dey/kodi-splitflap`, separate branch + PR):** Create `external-addons.json`; modify `tools/build_repo.py`, `repository.splitflap/addon.xml`; add test in its `tests/`

**Interfaces:**
- Produces: release asset URL `https://github.com/<owner>/<repo>/releases/download/v<version>/script.floppy-<version>.zip`; `external-addons.json` = `[{"id": "script.floppy", "zip_url": "...", "sha256": "..."}]`.

- [ ] **Step 1: Write failing test** `test_tag_must_match_addon_version` — `check_version("v1.2.0", addon_xml_with("1.2.0"))` passes; `"v1.2.1"` raises `SystemExit`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** `tools/check_version.py`; `release.yml` on tag `v*`: check version, run tests, `mise run zip`, create GitHub Release with the zip and print its sha256. **Step 4: Run** → PASS. **Step 5: Commit** `ci: release workflow`.
- [ ] **Step 6 (kodi-splitflap): Write failing test** `test_external_addon_included` — with a local zip served via `file://` URL in a temp `external-addons.json`, `build_repo` writes `docs/repo/script.floppy/script.floppy-<v>.zip` and an `<addon id="script.floppy">` entry in `addons.xml`; a wrong `sha256` or a zip whose `addon.xml` id differs fails the build.
- [ ] **Step 7: Implement** in `build_repo.py`: after local discovery, download each manifest entry (`urllib`), verify sha256, read `<id>/addon.xml` from the zip, append to addons.xml, copy zip into the datadir. Update `repository.splitflap/addon.xml` summary/description to cover more than the split-flap addons (keep id and URLs — existing installs keep working), bump its version to `1.1.0`.
- [ ] **Step 8: Run** its CI commands (`uv run ruff check . && uv run pyright && uv run pytest`) → PASS. Commit on a branch, open a PR for the user to review. Updating `external-addons.json` (URL + sha256) is the per-release step, documented in this repo's README.
