# Floppy for Kodi (`script.floppy`)

A Kodi 21 (Omega) addon that keeps Kodi and a self-hosted
[Floppy](https://floppy.asm0dey.site) media tracker in sync, both ways.

## What syncs

- **Playback**: start, pause and stop are scrobbled; Floppy's Now Playing card
  follows the player, and finished items get one play.
- **Watched state**: marking items watched or unwatched in Kodi (context menu
  or Kodi's own UI) goes to Floppy; changes made in Floppy (web UI, other Kodi
  boxes) reach Kodi on the next sync (every 5 minutes by default, on wake,
  and after playback stops).
- **Resume points** travel both ways.

The first sync on a device merges, never replaces: it shows a preview of what
it will change and writes nothing until you confirm. Writes made while Floppy is
unreachable are queued and sent later.

## Install

1. Download `script.floppy-<version>.zip` (or build it with `mise run zip`).
2. Kodi: Settings → Add-ons → Install from zip file → pick the zip.
3. Open the addon's settings, set **Server URL** to your Floppy instance.
4. **Log in**: Kodi shows a code and a URL; open the URL on any device, enter
   the code and approve. Advanced settings level also offers an API token
   instead of logging in.

Settings also hold **Sync now**, **Restore from Floppy**, **Push Kodi history to
Floppy**, **Log out**, and toggles for scrobbling, pull sync and sync toasts.

## Running alongside Trakt

While you trial this addon next to the Trakt addon (`script.trakt`):

1. In Floppy, turn **off** the recurring Trakt import (Settings → Integrations →
   Trakt). Keep it only if you also watch on non-Kodi Trakt clients: for those,
   the import is their only path into Floppy.
2. In `script.trakt`, keep only the **scrobbler** and turn **off** its
   Trakt → Kodi watched/playcount sync. This addon cannot tell those library
   writes from a watched toggle you made yourself, so it would push each of them
   to Floppy as a new play.

## Development

`mise run test`, `mise run lint`, `mise run zip`. The Kodi-only parts are
checked by hand: see [docs/manual-test.md](docs/manual-test.md).

## Releasing

1. Bump `version` in `addon.xml`, commit.
2. Tag `vX.Y.Z` (must equal the addon version) and push the tag. The
   `release` workflow tests, builds the zip and publishes it as a GitHub
   Release asset together with a `.sha256` file.
3. In `asm0dey/kodi-splitflap`, update `external-addons.json` with the new
   `zip_url`
   (`https://github.com/asm0dey/script.floppy/releases/download/vX.Y.Z/script.floppy-X.Y.Z.zip`)
   and the `sha256` from the release.
