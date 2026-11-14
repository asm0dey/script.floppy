# Manual test on a real Kodi 21

The Kodi side (settings screen, dialogs, player callbacks, library notifications)
cannot run in CI. Run this list on a real Kodi 21 (Omega) box against
`https://floppy.asm0dey.site` before each release. Watch the Kodi log
(`kodi.log`, lines starting with `[Floppy]`) while you go.

## Setup

0. If the Trakt addon is installed: in Floppy turn off the recurring Trakt
   import (Settings → Integrations → Trakt) unless you also watch on non-Kodi
   Trakt clients, and in `script.trakt` keep only the scrobbler, with its
   Trakt → Kodi watched/playcount sync **off** (see README, "Running alongside
   Trakt").

1. `mise run zip`, copy `dist/script.floppy-<version>.zip` to the box.
2. Settings → Add-ons → Install from zip file → pick the zip. Expect no errors
   and the service to start (`[Floppy] service started` in the log).
3. Add-on settings: set **Server URL** to `https://floppy.asm0dey.site`.

## Login and first sync

4. **Log in**: a dialog shows a verification URL and a code. Open the URL on a
   phone, enter the code, approve. Expect the toast "Logged in to Floppy".
5. Within a few seconds a yes/no dialog shows the first-sync preview
   ("Push N to Floppy, mark M watched in Kodi, unwatch K"). Check that the
   counts are plausible for this library. Answer **No** first: nothing in Kodi
   or Floppy changes, and the dialog does not come back on its own (wait one
   sync interval). Then **Sync now** and answer **Yes**: a toast repeats
   the summary and Kodi's watched marks match Floppy.

## Scrobbling

6. Play a movie for a minute: Floppy's Now Playing card shows it. Pause and
   resume: the card follows. Watch it to the end. Floppy shows one play of it.
   Play another movie, seek to about 92 % and stop: Floppy shows it watched,
   and on a second box (or after **Sync now**) it has no resume point.
7. Repeat with an episode.
8. With the Trakt addon's scrobbler running alongside (step 0), check that
   Floppy still shows **one** play per watch, not two.

## Watched state, both ways

9. Context menu on an unwatched movie shows "Mark watched in Floppy"; use it.
   Kodi marks it watched at once, Floppy shows it watched within seconds.
   On the same (now watched) movie the menu shows "Mark unwatched in Floppy";
   use it and check Floppy again.
10. Context menu "Toggle watched in Floppy" on a season and on a show: every
    episode flips in Kodi and in Floppy.
11. Toggle watched with Kodi's own UI (no Floppy menu item): Floppy follows.
12. Unwatch an item in the Floppy web UI: Kodi shows it unwatched within the
    sync interval (5 min by default). Mark something watched in Floppy: Kodi
    follows too.
13. Add a movie to Kodi's library that Floppy already has as watched, and run a
    library scan: after the scan it shows as watched in Kodi, with no dialog.

## Restore and several boxes

14. Uninstall the addon with its data (or delete
    `userdata/addon_data/script.floppy`), reinstall from the zip, log in.
    The preview appears again; confirm: Kodi's state is back as it was.
15. On a library with at least 500 watched items in Floppy, run **Restore from
    Floppy** and confirm. Afterwards the outbox is empty (no `[Floppy]`
    "retrying later" lines, the next sync sends nothing) and Floppy's play
    counts are unchanged: restoring must not echo plays back.
16. **Restore from Floppy** and **Push Kodi history to Floppy** each show the
    preview dialog and apply only after **Yes**.
17. On a second Kodi box with the addon logged in to the same account, mark an
    item watched on the first box: the second box shows it within its sync
    interval.

## Settings and errors

18. Turn **Scrobble playback** off and play something: no Now Playing card.
19. Turn **Sync watched state from Floppy** off: Floppy web changes no longer
    reach Kodi; **Sync now** still works.
20. **Log out**: toast "Logged out of Floppy"; Floppy no longer lists the
    device's token as active. Log in again works.
21. Revoke the device in Floppy's web UI: once its access token expires, one
    "Please log in again" toast, no repeats; logging in clears it.
22. Turn off the network for a sync interval, mark items watched meanwhile,
    turn it back on: the changes reach Floppy (outbox), no toasts while offline.
