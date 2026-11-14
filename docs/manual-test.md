# Manual test on a real Kodi 21

The Kodi side (settings screen, dialogs, player callbacks, library notifications)
cannot run in CI. Run this list on a real Kodi 21 (Omega) box against
`https://floppy.asm0dey.site` before each release. Watch the Kodi log
(`kodi.log`, lines starting with `[Floppy]`) while you go.

## Setup

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
   or Floppy changes. Then **Sync now** and answer **Yes**: a toast repeats
   the summary and Kodi's watched marks match Floppy.

## Scrobbling

6. Play a movie for a minute: Floppy's Now Playing card shows it. Pause and
   resume: the card follows. Watch it to the end (or skip to the last 30 s
   and stop). Floppy shows one play of it.
7. Repeat with an episode.
8. With the Trakt addon still installed and scrobbling alongside, check that
   after the next Trakt import Floppy still shows **one** play per watch, not two.

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
15. **Restore from Floppy** and **Push Kodi history to Floppy** each show the
    preview dialog and apply only after **Yes**.
16. On a second Kodi box with the addon logged in to the same account, mark an
    item watched on the first box: the second box shows it within its sync
    interval.

## Settings and errors

17. Turn **Scrobble playback** off and play something: no Now Playing card.
18. Turn **Sync watched state from Floppy** off: Floppy web changes no longer
    reach Kodi; **Sync now** still works.
19. **Log out**: toast "Logged out of Floppy"; Floppy no longer lists the
    device's token as active. Log in again works.
20. Revoke the device in Floppy's web UI: once its access token expires, one
    "Please log in again" toast, no repeats; logging in clears it.
21. Turn off the network for a sync interval, mark items watched meanwhile,
    turn it back on: the changes reach Floppy (outbox), no toasts while offline.
