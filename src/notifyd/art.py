"""Cover art from http(s) URLs, for the notification center's now-playing row only.

Players such as Spotify give `mpris:artUrl` as https://i.scdn.co/...; the center shows it once it is
here. Downloads run in a thread (TIMEOUT seconds, at most MAX_BYTES) and land in
$XDG_CACHE_HOME/notifyd/art/, named by the URL's hash; the cache keeps the KEEP newest files.
Notification images never come from the network: a sender cannot make the daemon fetch a URL.
"""

import hashlib
import os
import threading
import urllib.error
import urllib.request
from pathlib import Path

from gi.repository import GLib

TIMEOUT = 3            # seconds
MAX_BYTES = 2 * 1024 * 1024
KEEP = 200
USER_AGENT = 'notifyd'

_pending = set()       # URLs being fetched
_lock = threading.Lock()


def remote(url):
    return isinstance(url, str) and url.startswith(('http://', 'https://'))


def cache_dir():
    return Path(os.environ.get('XDG_CACHE_HOME') or Path.home() / '.cache') / 'notifyd' / 'art'


def cache_path(url):
    return cache_dir() / hashlib.sha1(url.encode()).hexdigest()


def cached(url):
    """The cached file for URL, or None."""
    path = cache_path(url)
    return path if path.is_file() else None


def fetch(url, on_done, deliver=GLib.idle_add):
    """Fetch URL in a thread unless cached or under way; ON_DONE(path or None) runs through DELIVER
    (the main loop by default). Returns the cached path right away when there is one."""
    if not remote(url):
        return None
    path = cached(url)
    if path:
        return path
    with _lock:
        if url in _pending:
            return None
        _pending.add(url)
    threading.Thread(target=_download, args=(url, on_done, deliver), daemon=True).start()
    return None


def _download(url, on_done, deliver):
    path = None
    try:
        path = _get(url)
    finally:
        with _lock:
            _pending.discard(url)
        deliver(lambda: on_done(path) and False)


def _get(url):
    request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            data = response.read(MAX_BYTES + 1)
    except (urllib.error.URLError, OSError, ValueError):
        return None
    if not data or len(data) > MAX_BYTES:
        return None
    path = cache_path(url)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + '.tmp')
        tmp.write_bytes(data)
        tmp.replace(path)
        _prune(path.parent)
    except OSError:
        return None
    return path


def _prune(directory):
    files = sorted((p for p in directory.iterdir() if p.is_file() and not p.name.endswith('.tmp')),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    for old in files[KEEP:]:
        try:
            old.unlink()
        except OSError:
            pass
