# fieldstation42-live — Architecture

Status: approved design (2026-10-14). Nothing here is built yet.

## Goal

Run FieldStation42 stations as network streams that Jellyfin can ingest as Live TV
(M3U tuner + XMLTV guide), including a guide channel and a WeatherStar-style weather
channel, while keeping upstream's local (mpv) playback working from the same config.

## What upstream already gives us

| Piece | Where | Reusable for streaming? |
|---|---|---|
| Station configs, catalogs, schedules | `confs/`, `catalog/`, `runtime/`, `fs42/liquid_*` | Yes, unchanged |
| "What's on now" lookup | `LiquidManager.get_play_point(network, when)` → `PlayPoint(index, offset, plan)` where each plan entry has `path`, `skip`, `duration` | Yes. This is the core hook, and it has no mpv dependency |
| Schedule listings for a time range | `LiquidAPI.get_all_blocks(start, end)`, exposed at `GET /schedules/all` | Yes, the source for XMLTV |
| Web console + REST API | `fs42/fs42_server` (FastAPI + uvicorn) | Yes; we add a router rather than build a second server |
| Web-rendered guide pages | `static/guide.html`, `static/customguide/` (80s/90s/00s themes, music) | Yes, rendered headlessly |
| Web page → HLS proof of concept | `page_stream/` (Xvfb + Chromium + ffmpeg shell scripts) | Technique yes, scripts no |
| Player | `field_player.py` + `fs42/station_player.py` | No. It models one TV (one mpv, one current channel, channel-change loop) |

The upstream `streaming` network type plays external streams into FS42. It does not
send anything out.

## Design

```
                     ┌────────────────────── fs42 container ───────────────────────┐
 Jellyfin ──GET────► │ FastAPI (upstream server + fs42_live router)                │
 (M3U tuner,         │   /live/channels.m3u    /live/xmltv.xml                     │
  XMLTV)             │   /live/stream/{ch}.ts ──► ChannelHub ──► viewers (fan-out) │
                     │                              │                               │
                     │              ┌───────────────┴───────────────┐               │
                     │        ScheduledSource                  WebPageSource        │
                     │  get_play_point(now) → ffmpeg      Xvfb + Chromium → ffmpeg  │
                     │  per segment (QSV encode)          (guide, weather)          │
                     └──────────────────────────────────────────────────────────────┘
```

### 1. Channel sources

All sources write to the same normalized output, so each client sees one continuous
stream no matter what the underlying files look like. The output profile is
configurable globally and per station. Default (retro): 640x480 4:3, 29.97 fps, H.264,
AAC stereo 48 kHz, MPEG-TS. Widescreen sources are letterboxed and 4:3 sources pass
through without bars. A 720p 16:9 profile stays available for channels that want it.

- **ScheduledSource** (normal stations). Loop: ask `get_play_point(now)`, then run one
  ffmpeg for the current segment with `-ss skip+offset -t remaining`. When it exits,
  ask again. Re-querying the wall clock at every segment boundary corrects drift
  (ffmpeg start-up time and rounding never accumulate). The output timestamp offset
  keeps PTS monotonic across segments. Mixed sources (4:3 TV rips, commercials,
  bumps) get scaled and padded to the profile. Encoding uses Intel Quick Sync
  (VAAPI/QSV) through `/dev/dri`.
- **WebPageSource** (guide, weather, any `web` station). Xvfb + Chromium in kiosk mode
  pointed at a URL, captured with ffmpeg `x11grab`. Audio comes from the station's
  music playlist (the guide already has one) or a PulseAudio null sink. Guide = the
  FS42 guide page served by our own API. Weather = WS4KP (already running on the
  Beelink at :9090, or bundled later).
- **Off-air / stuck**: falls back to the station's standby image or off-air media,
  never a dead connection. Jellyfin and the Rokus handle a stalled stream much worse
  than an ugly one.

### 2. ChannelHub (on-demand, shared)

- The first viewer of channel N starts its source. Later viewers attach to the same
  output: one encode, many readers, with a small ring buffer so a new viewer starts
  on a keyframe.
- The source stops once the last viewer has been gone for a grace period (~30 s), so
  quick channel flips don't restart it.
- Idle cost is zero. Because schedules are wall-clock based, a channel nobody is
  watching is still "on". Tuning in just computes where it would be.

### 3. Jellyfin integration

- `GET /live/channels.m3u`: one entry per non-hidden station with `tvg-id`, `tvg-chno`
  (FS42 channel number), `tvg-name`, `tvg-logo`, and the stream URL.
- `GET /live/xmltv.xml`: programmes from `LiquidAPI.get_all_blocks` (title, episode
  metadata where the NFO/TMDB data has it) for the next N hours. Web channels get a
  single repeating "Local Forecast" / "Program Guide" entry.
- Jellyfin setup: Live TV → add M3U tuner + XMLTV provider pointing at those URLs.
  Remote viewers (Tailscale, Onn stick) work through Jellyfin as they do today.

### 4. Local mode

Unchanged: `field_player.py` with mpv, exactly as upstream. The same `confs/`,
catalogs and schedules drive both modes. A box hooked straight to a TV runs the
player; the server runs the live router. In principle one install can do both.

### 5. Control panel

Extend the upstream web console instead of building a new one:
- a "Live" page: stream status per channel (idle / encoding / viewers), M3U/XMLTV
  links, encode profile, restart a channel
- later: content rotation settings, commercials import, weather location

## Code layout and upstream hygiene

- All new code goes in `fs42_live/`, a sibling of upstream's `fs42/` package. That
  keeps merges from upstream (which is very active) conflict-free.
- Upstream touch points, kept as small as possible:
  1. register the `fs42_live` router in `fs42_server.py` (a few lines; it could even
     be an upstream PR as a generic plugin hook)
  2. a nav link in the web console
- Packaging: `fs42_live/docker/` with a headless image (Python, ffmpeg with Intel
  media driver, Chromium, Xvfb), plus a compose file and setup script. Upstream's
  `docker/` assumes a local X display and stays as-is.

## Deployment notes (Beelink)

- Catalogs store absolute media paths, so media is mounted at the same path inside
  the container (`/mnt/storage:/mnt/storage:ro`).
- `/dev/dri` passed through for Quick Sync, the same way Jellyfin has it today.
- 480p encodes are cheap. The N100 should handle many at once; measure in M2.
- Leftover experiments (tunarr, threadfin, rtmp-server, pagecaster) can be retired
  once this covers them.

## Milestones

| | Milestone | Done when |
|---|---|---|
| M0 | Baseline: stock FS42 in a container, Miami Vice station, catalog + schedule built | Web console shows a correct schedule |
| M1 | One channel streaming (`/live/stream/{ch}.ts`) | Plays in VLC on the Windows PC across a segment boundary |
| M2 | M3U + XMLTV, fan-out, on-demand lifecycle, Quick Sync | Channel and guide data in Jellyfin; plays on Roku and over Tailscale |
| M3 | Guide channel as a stream | Retro guide playable in Jellyfin |
| M4 | Weather channel | WS4KP forecast with music playable in Jellyfin |
| M5 | Live page in the control panel + packaging | A fresh Beelink is set up from the compose file + script |
| M6 | Content rotation, commercials import pipeline | |

## Open questions

1. Should reception/scramble effects (mpv filters upstream) carry over to streams?
   Possible later via ffmpeg filters; out of scope for v1.
2. Upstream: once M2 works, open a GitHub Discussion with Shane about a plugin hook
   and/or contributing the live output.
