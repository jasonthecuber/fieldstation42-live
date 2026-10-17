"""Put a message on the air from the command line (in the container):

    python3 fs42_live/tools/say.py crawl 12 "Hi Doug!"            # crawl on channel 12 for 30 minutes
    python3 fs42_live/tools/say.py crawl all "Hi Doug!" -m 60     # on every channel, for an hour
    python3 fs42_live/tools/say.py greeting "Hi Doug!" "Jason is showing off his retro TV"
    python3 fs42_live/tools/say.py crawl 12 ""                    # take it off
    python3 fs42_live/tools/say.py                                # what's on now
"""

import argparse
import json
import urllib.request

API = "http://localhost:4242/live/messages"

parser = argparse.ArgumentParser()
parser.add_argument("kind", nargs="?", choices=["crawl", "greeting"])
parser.add_argument("args", nargs="*", help="crawl: channel (or all), text; greeting: text, second line")
parser.add_argument("-m", "--minutes", type=float, default=30)
a = parser.parse_args()

if a.kind == "crawl":
    channel, text = (a.args + ["", ""])[:2]
    body = {"kind": "crawl", "channel": channel or "all", "text": text, "minutes": a.minutes}
elif a.kind == "greeting":
    text, sub = (a.args + ["", ""])[:2]
    body = {"kind": "greeting", "text": text, "sub": sub, "minutes": a.minutes}
if a.kind:
    request = urllib.request.Request(API, json.dumps(body).encode(), {"Content-Type": "application/json"})
    print(json.dumps(json.load(urllib.request.urlopen(request)), indent=1))
else:
    print(json.dumps(json.load(urllib.request.urlopen(API)), indent=1))
