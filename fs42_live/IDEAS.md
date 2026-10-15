# Ideas backlog

Unsorted and unplanned. Jason's brain dump from 2026-10-14, with first notes from Claude.
Milestones live in ARCHITECTURE.md; anything here gets promoted there once it's planned.

## Guide

- **A second guide channel in the Prevue / TV Guide Channel style**: video preview with promo
  text on top, a scrolling grid below. Let each install turn on either guide or both.
  *Note:* doable as a web channel. A Chromium page can play another channel's stream in a
  `<video>` element above our own grid. Needs HLS output (see Players).
- **Episode titles in the retro guide.** Miami Vice shows only "Miami Vice". The episode name
  is in the catalog (e.g. "S01E23 Lombard"), so listings could carry it. It might not fit;
  try "Miami Vice: Lombard" and see how the theme truncates it.
- **Channel name with a space** ("Miami Vice", not "MiamiVice"). `network_name` is the key
  for the schedule, catalog and state files, so look for a display-name field before
  renaming it.

## Presentation (HBO-style)

- **Christmas specials and ads in December** (Noel's idea). archive.org item
  `80s-christmas-specials-with-commercials-3` is already picked out. Needs seasonal
  commercial folders, or date ranges on tags.
- **HBO-style intro before movies.**
- **"Next on HBO" slates** before features. *Note:* they were mostly static graphics
  (title, stars) over a background, so they can be generated per movie from catalog/NFO
  data: an HTML template rendered to a short clip, or ffmpeg text over an image. Mixing
  graphics over live video is also possible (ffmpeg overlay, or Chromium compositing),
  just more work.

## Players and apps

- **Web player over Tailscale before native apps.** A full-screen web app (a PWA, added to
  the home screen) avoids the App Store and Apple developer program, and works for Noel
  etc. on phones and PCs. Rokus still need a native app (no browser); the Onn could wrap
  the web app.
  - Immersive mode: feels like an old TV set. Channels plus the guide (which is a channel),
    channel up/down. Settings hidden.
  - Click a show in the guide to tune to it.
  - Needs HLS output: iOS Safari won't play our raw MPEG-TS.
- **On-demand mode**: pick any episode or movie and stream it (like Jellyfin), reachable
  from a hidden menu, not from immersive mode.
  - **Retro mode**: on-demand with the era's pre-show (HBO bumps) and commercial breaks
    built in. FS42's reel/break builder (`ShowCatalog.make_reel_block`) could build the
    breaks around a single episode.
  - **"Switch to stream mode"** from a live channel: jump to on-demand for what's playing,
    with or without retro mode.

## Streaming

- **Roku start-up is slower than other clients.** Jellyfin repackages our stream as HLS and
  the Roku buffers a few chunks first. Try a shorter keyframe interval, then our own HLS.
- **A ~0.5 s pause once on the Roku.** Note whether it happens at show/commercial switches
  (ours) or randomly (Roku buffering).
- **Skip missing files** "just in case": if a scheduled file was deleted, move to the next
  segment instead of showing the standby slate until the catalog watcher catches up.

## Packaging for Noel

- **Set up the whole box, not just fs42-live.** Noel's Beelink needs Jason's full stack
  (Jellyfin, qBittorrent behind gluetun/NordVPN, homepage, filebrowser) as well. The setup
  script should ask for the NordVPN WireGuard private key and write it to the stack's
  `.env` (`NORDVPN_PRIVATE_KEY`), alongside USER_ID/GROUP_ID/TIMEZONE/STORAGE_ROOT.
  Never commit the key.

## Content

- **Movies channel (ch 7):** Jason has a few retro movies in `/mnt/storage/Movies` (the "Old
  Drive"). Enough to start the channel, with HBO-style intros and "Next on" slates.
- **Shared '80s commercials:** move them out of `catalog/miami_vice/commercial/` into a shared
  folder (e.g. `catalog/_shared/commercials_80s`) and link it into each '80s channel, once a
  second channel needs them.
