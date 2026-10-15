# Setting up fieldstation42-live

This puts retro TV channels into Jellyfin's Live TV guide. It needs a Linux box with Docker. Intel Quick Sync is optional: without it, channels encode on the CPU.

## 1. Install

```sh
git clone <this repo> ~/fieldstation42-live
cd ~/fieldstation42-live/fs42_live/docker
./setup.sh
```

The script asks for four things and suggests an answer for each:

| Setting | What it is |
|---|---|
| Data folder | Where channel configs, catalogs and schedules live (default `~/fs42-data`) |
| Media folder | The folder that holds your shows, e.g. `/mnt/storage` |
| Time zone | The viewers' time zone; use the same one Jellyfin uses |
| Town | For the weather channel, e.g. `Austin TX USA` |

Then it builds and starts two containers: `fs42-live` and `fs42-ws4kp` (the WeatherStar 4000+ weather display). It also adds two starter channels:

- **Channel 3, TV Guide:** a scrolling retro guide
- **Channel 8, Weather:** the local forecast, with music

Running the script again is safe. It keeps your settings and your channels.

## 2. Connect Jellyfin

In Jellyfin, go to **Dashboard → Live TV**:

1. **Tuner devices → +** → **M3U Tuner**, file or URL: `http://<server>:4242/live/channels.m3u`
2. **TV guide data providers → +** → **XMLTV**, file or URL: `http://<server>:4242/live/xmltv.xml`
3. Click **Refresh guide data**.

The channels now appear under Live TV. A channel starts streaming when someone tunes in, and stops about 30 seconds after the last viewer leaves (`idle_grace` below).

To watch without Jellyfin, open `http://<server>:4242/live/watch` in a browser (phones too). Arrow keys or the on-screen buttons change channels, and typing a number tunes to it. It plays the same streams as Jellyfin (older iPhones get HLS instead). The channels on either side of the one playing are kept warm, so flipping up or down is instant (about 0.1 s); any other channel comes up in about 2 seconds.

Streaming settings go in a `"live"` section of `confs/main_config.json` (create the file if there isn't one); anything left out keeps its default, and they take effect when the container restarts:

```json
{ "live": { "idle_grace": 30, "surf_grace": 5, "warm_neighbours": 1, "page_load": 0.5 } }
```

- `idle_grace`: seconds a channel keeps running after its last Jellyfin viewer leaves (Jellyfin sometimes probes a channel, closes it and reopens it).
- `surf_grace`: the same for the web player, which keeps naming the channels it wants warm every 2 seconds, so keep this at 3 or more.
- `warm_neighbours`: how many channels on each side the web player warms; 0 turns it off. Each warm web channel (guide, weather, Coming Soon) costs about half a CPU core on an N100, a show channel about a tenth.
- `page_load`: seconds a web channel's page gets to draw before capture starts. A station can override it with `live_page_load`.

## 3. Add show channels

A channel's content lives in a folder under the data folder's `catalog/`. Each subfolder becomes a tag that the schedule can use. Link your shows in rather than copying them:

```sh
mkdir -p ~/fs42-data/catalog/miami_vice/commercial ~/fs42-data/catalog/miami_vice/bump
ln -s "/mnt/storage/TV/Miami Vice" ~/fs42-data/catalog/miami_vice/miamivice
```

`commercial/` and `bump/` hold the clips that fill the breaks. To fill `commercial/` with real '80s ads, import compilations from archive.org. Each one is split into single spots with even volume:

```sh
docker exec fs42-live python3 fs42_live/tools/import_commercials.py catalog/miami_vice/commercial 1981-commercial-breaks
docker exec fs42-live python3 fs42_live/tools/import_commercials.py catalog/miami_vice/commercial \
    Vintage_Commercials_Advertisements_80s "1987-09-12 NBC Ads" "WPLG10"
```

The second argument is the archive.org item (the part of its address after `/details/`). Any further arguments pick only the files whose names contain them. Other good items: `one-hour-of-1980s-tv-commercials-80s-commercial-compilation-1` and `television-beer-commercials-1970s-1980s`. For a kids' channel, use the Saturday-morning files of `Vintage_Commercials_Advertisements_80s`.

Bumpers ("Miami Vice will continue…") are too few per tape to split automatically, so they're cut by hand. The four come from archive.org items `capture-a-2454` (KRON, at 64.2–71.1, 160.0–165.3 and 169.4–172.6 s) and `nbc-commercial-breaks-september-12-1986` (KING, at 296.1–303.1 s), saved with `save_spot` from the importer. Bumps in `bump/` play on either side of a break. To keep one to its side, put it in `bump/pre/` (going into the break) or `bump/post/` (coming back). The two that say "will continue" (`kron_1986_boat_1`, `king_1986_street`) are in `bump/pre/`.

Until you have real ones, this makes color-bar stand-ins for both folders:

```sh
docker exec fs42-live sh fs42_live/tools/make_placeholder_reels.sh catalog/miami_vice "MIAMI VICE"
```

The server notices new or removed files in a station's folder within the hour (or right away on `docker restart fs42-live`) and updates its catalog. New clips are worked into the schedule as time is added; if files were removed, the schedule is rebuilt. Rebuilding a catalog from the web console works too, but it always clears the schedule.

The server also finds each episode's act breaks (where the picture fades to black and the sound drops), so commercial breaks land where the network put them. Each episode is scanned once, about a minute apiece at low priority. A new library takes a while, and the schedule is rebuilt once its episodes are done. Episodes without clear fades keep evenly spaced breaks.

Then open the web console at `http://<server>:4242/` and go to **Stations**:

1. Add a station with `content_dir` set to `catalog/miami_vice`. Give it a short name: Jellyfin's TV guide shows only about one word of it. The name is also the station's internal key, so to show it differently (e.g. with a space), add `"live_display_name": "Miami Vice"` to the station's config.
2. Build its catalog.

Schedules are built a week ahead and extended automatically. The **Live** page shows each channel's status and how far its schedule is built.

After adding or changing stations, restart the server (`docker restart fs42-live`) and refresh Jellyfin's guide data.

## Placeholder channels

To reserve a channel number with a "Coming Soon" slate and music:

```sh
../tools/add_coming_soon.sh ~/fs42-data/confs 4 "Classic Cartoons" Cartoons
docker restart fs42-live
```

The guide lists it as "Classic Cartoons - Coming Soon". When the real channel is ready, delete `coming_soon_classic_cartoons.json` and add the station.

## Updating

```sh
cd ~/fieldstation42-live && git pull
cd fs42_live/docker && docker compose up -d --build
```

## Costs

- **A show channel:** about a tenth of a CPU core while someone watches.
- **Guide, weather or Coming Soon channel:** about half a core while someone watches, because a browser draws the page.
- **Idle:** nothing; no channel uses anything while nobody is watching.
