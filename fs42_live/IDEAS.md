# Ideas backlog

Unsorted and unplanned. Jason's brain dump from 2026-10-14, with first notes from Claude.
Milestones live in ARCHITECTURE.md; anything here gets promoted there once it's planned.

## Guide

- **A second guide channel in the Prevue / TV Guide Channel style**: video preview with promo
  text on top, a scrolling grid below. Let each install turn on either guide or both.
  **First version done 2026-10-15**: ch 2 "Preview" (`/live/preview_guide.html`), a web
  channel that plays the scheduled channels in turn (40 s each, `?rotate=`) above a scrolling
  grid built from our XMLTV. Later: promo text per show, movies in their own colour, real
  Prevue-style music.

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
  - **"Report a problem" in the menu** (Jason, 2026-10-16). A viewer (Noel, family) writes what's wrong;
    the server saves it with the channel, the time and the file playing at that moment (from the
    schedule, with the position in it), so it can be found and fixed later. Reports show on the
    live page, and Claude can read them from the server. A description is required.
    - Stamp the moment the menu opened, not when the form is sent, and save everything that
      played in the half minute before it (the problem may be a few seconds back).
    - Quick picks, since typing on a TV is painful: number keys on the remote (the Samsung
      passes digits, not letters), e.g. 1 wrong bumper, 2 static or frozen, 3 sound, 4 wrong
      show or listing, 5 other. A pick counts as the description; typing more is optional.
  - Test on the Onn with a TV browser (TV Bro): d-pad up/down surfs, OK goes full screen.
  - **Theater room**: the Onn stick with a browser now; later a small Android TV app that
    wraps `/live/watch` in a full-screen WebView (no browser bars, starts from the home
    screen, maps the remote's channel keys). Far less work than a Roku app (M8), and it
    covers the Onn at mom's too.
  - **Installing apps without a store** (2026-10-15). Android TV: sideload the APK with the
    free Downloader app from a URL our server hosts; no developer account (Google Play's is
    $25 once, only for the store). Roku can't run Android apps; it needs a separate native
    BrightScript/SceneGraph app. Sideload it in developer mode (remote code Home x3, Up x2,
    Right, Left, Right, Left, Right; upload a zip to the Roku's IP), free, one sideloaded app
    per Roku. Or publish a beta channel from a free Roku developer account: shareable by
    link, but beta channels have expired after about 120 days, so check Roku's current rules.
    Roku plays HLS well (not our raw TS), so a Roku app would use `/live/hls/`, with channel
    changes of maybe 2-3 s. Jason is leaning browser-only (an Onn stick per TV) over native apps.
- **Mobile layout for the web console.** The live page and upstream's pages stack into tall
  cards on a phone; the channel table should be a compact list.
- **On-demand mode**: pick any episode or movie and stream it (like Jellyfin), reachable
  from a hidden menu, not from immersive mode.
  - **Retro mode**: on-demand with the era's pre-show (HBO bumps) and commercial breaks
    built in. FS42's reel/break builder (`ShowCatalog.make_reel_block`) could build the
    breaks around a single episode.
  - **"Switch to stream mode"** from a live channel: jump to on-demand for what's playing,
    with or without retro mode.
  - Scope (2026-10-15 brainstorm): keep on-demand to the FS42 catalog, where retro mode is the
    point. For the rest of the library, Jellyfin already does it well (continue watching,
    users, metadata), so link out to it. If anything, a per-device "resume" for retro mode.
    A series rolls on into its next episode, breaks and all.

## Server landing page

- **Replace the stock Homepage (gethomepage.dev) placeholders** with real links: Jellyfin,
  the FS42 console, live page and watch player, filebrowser, qBittorrent. Homepage also has
  widgets for Jellyfin (now playing) and qBittorrent (speeds). It's Jason's container: Claude
  drafts `services.yaml`, Jason installs it.

## Content helpers

- **Wishlist → archive.org fetcher**: a file of wanted items (commercial reels, bumpers,
  specials, public-domain cartoons and movies) that a tool searches on archive.org and
  downloads into the right catalog folder, like `import_commercials.py`. A small UI later.
- **Disc ripping station** (Jason, 2026-10-16; flea-market DVDs and Blu-rays). Automatic Ripping
  Machine (ARM, Docker) on the Beelink with a USB drive: insert a disc, it identifies, rips
  (MakeMKV), optionally transcodes (HandBrake, Quick Sync), names and ejects. Movies file
  cleanly; TV box sets land in a "to sort" folder for the sorter below. The host setting (udev)
  and /mnt/storage writes are Jason's side: Claude drafts, Jason applies. Check the Blu-ray
  drive model against MakeMKV before buying.
- **Sorter for finished downloads**: move or link completed files into the right show/catalog
  folder with clean names. Claude won't build torrent searching.

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

- **Music channel (ch 9)** is live (2026-10-15): 80s and 90s videos with MTV-style captions and
  80s commercials, and a generated "FS42 Music Videos" ident as its bump. Later: real MTV-era
  idents in `catalog/music/bump`, and split by decade into two channels once there are more
  videos.

- **Movies channel (ch 7)** is live (2026-10-15): 9 retro movies from the Old Drive and the
  torrents folder, starting on the quarter hour, with "Our Feature Presentation" before each
  and a break of FS42 idents and '80s ads after. Later: real '80s movie trailers in the breaks
  instead of ads (archive.org has trailer reels), "Next on" slates per movie, and A Christmas
  Story only in December.
- **Pan and scan for wide movies** (Jason, 2026-10-15). Scope movies (2.39:1, e.g. Blazing
  Saddles) letterboxed into 4:3 come out tiny, with bars on all four sides in a 16:9 player.
  Old TV cropped them to fill the screen, which loses almost half the width. A per-station
  setting: letterbox (now), full crop, or a middle ground that crops the sides to about 16:9 and
  keeps thin bars.
- **Even out loudness per file.** **Done 2026-10-16** (fs42_live/loudness.py, `loudness_target` -24). The movies
  range from -19 to -29 LUFS (See No Evil is the quiet one); the ads are -22. Measure each
  file once, like the act-break scan, and apply a gain. Web channels (guide music) aren't levelled yet.
- **Shared '80s commercials:** move them out of `catalog/miami_vice/commercial/` into a shared
  folder (e.g. `catalog/_shared/commercials_80s`) and link it into each '80s channel, once a
  second channel needs them.
- **Show-specific bumpers hidden among the ads** (Jason, 2026-10-16): spots like "now back to Alvin
  and the Chipmunks" or ABC's "now back to Superman" came in with the archive.org ad reels and
  play on any channel. Find them (short, network logo, voice-over; or via viewer reports) and move
  them out, or keep them for a matching channel.
