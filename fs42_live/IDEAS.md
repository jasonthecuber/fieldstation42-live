# Ideas backlog

Unsorted and unplanned. Jason's brain dump from 2026-10-14, with first notes from Claude.
Milestones live in ARCHITECTURE.md; anything here gets promoted there once it's planned.

## Guide

- **A second guide channel in the Prevue / TV Guide Channel style**: video preview with promo
  text on top, a scrolling grid below. Let each install turn on either guide or both.
  *Note:* doable as a web channel. A Chromium page can play another channel's stream in a
  `<video>` element above our own grid. HLS output is done (`/live/hls/<ch>/index.m3u8`).

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
  - HLS output and a first player are done (`/live/watch`, 2026-10-15); this would grow it
    into an installable PWA. Full screen (double-tap, button, OK on a remote) and a
    home-screen manifest are done too.
  - **Breaking out to a menu** (Jason, 2026-10-15). A visible way for everyday things: OK on
    a TV remote or a ☰ button beside ▲▼ on a phone opens a menu over the picture (on-demand,
    guide, settings), and Back closes it. Phone swipes like down-up-down on the left edge
    can't be found by anyone who doesn't already know them, so they'd suit the hidden modes,
    e.g. a remote code like up-up-down-down (Konami style) on a TV. Moving full screen off OK
    once OK opens the menu.
  - Test on the Onn with a TV browser (TV Bro): d-pad up/down surfs, OK goes full screen.
  - **Theater room**: the Onn stick with a browser now; later a small Android TV app that
    wraps `/live/watch` in a full-screen WebView (no browser bars, starts from the home
    screen, maps the remote's channel keys). Far less work than a Roku app (M8), and it
    covers the Onn at mom's too.
- **Mobile layout for the web console.** The live page and upstream's pages stack into tall
  cards on a phone; the channel table should be a compact list.
- **On-demand mode**: pick any episode or movie and stream it (like Jellyfin), reachable
  from a hidden menu, not from immersive mode.
  - **Retro mode**: on-demand with the era's pre-show (HBO bumps) and commercial breaks
    built in. FS42's reel/break builder (`ShowCatalog.make_reel_block`) could build the
    breaks around a single episode.
  - **"Switch to stream mode"** from a live channel: jump to on-demand for what's playing,
    with or without retro mode.

## Streaming

- **Roku start-up is slower than other clients.** Jellyfin repackages our stream as HLS and
  the Roku buffers a few chunks first. Keyframes are every 1 s now (was 2 s, 2026-10-15); if still slow, our own HLS.
- **A ~0.5 s pause once on the Roku.** Note whether it happens at show/commercial switches
  (ours) or randomly (Roku buffering).
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
