"""Tells Jellyfin to fetch the guide (XMLTV) again whenever the schedules change.

Jellyfin's own "Refresh Guide" task runs once a day, so a rebuilt schedule would leave its
guide wrong until then. A change asks for a refresh a minute later, so a burst of changes
(several stations extended, one after another) makes one request.

It needs a Jellyfin API key (Dashboard > API Keys), mounted at KEY_FILE. Without one this
does nothing.
"""

import json
import logging
import threading
import urllib.error
import urllib.request

from fs42_live import settings

_l = logging.getLogger("FS42LIVE")

KEY_FILE = "/run/secrets/jellyfin_key"
WAIT = 60  # seconds after a change before asking

_lock = threading.Lock()
_pending = None


def refresh_soon():
    global _pending
    with _lock:
        if _pending is not None:
            return
        _pending = threading.Timer(WAIT, _refresh)
        _pending.daemon = True
        _pending.start()


def _key():
    try:
        with open(KEY_FILE) as f:
            return f.read().strip() or None
    except OSError:
        return None


def _call(key, path, method="GET"):
    # this version of Jellyfin only takes the key in this header (X-Emby-Token and ?api_key get a 401)
    req = urllib.request.Request(settings.get("jellyfin_url").rstrip("/") + path, method=method,
                                 headers={"Authorization": f'MediaBrowser Token="{key}"'})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def _refresh():
    global _pending
    with _lock:
        _pending = None
    key = _key()
    if not key:
        return
    try:
        tasks = json.loads(_call(key, "/ScheduledTasks?isHidden=false"))
        task = next((t for t in tasks if t.get("Key") == "RefreshGuide"), None)
        if task is None:
            _l.warning("jellyfin guide: Jellyfin has no Refresh Guide task")
            return
        _call(key, f"/ScheduledTasks/Running/{task['Id']}", method="POST")
        _l.info("jellyfin guide: schedules changed; asked Jellyfin to refresh its guide")
    except urllib.error.HTTPError as e:
        _l.warning(f"jellyfin guide: Jellyfin refused the refresh (HTTP {e.code})")
    except Exception as e:  # Jellyfin down or unreachable: its daily refresh still catches up
        _l.warning(f"jellyfin guide: couldn't reach Jellyfin ({str(e)[:80]})")
