"""Cover art fetched over http(s) for the center: cache, limits, one download per URL.

A local HTTP server serves a small PNG, an oversized body, a slow response and a 404.
"""

import http.server
import threading
import time
from typing import ClassVar

import gi
import pytest

gi.require_version('GdkPixbuf', '2.0')
from gi.repository import GdkPixbuf  # noqa: E402

from notifyd import art  # noqa: E402


def png_bytes():
    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 4, 4)
    pixbuf.fill(0x3366ccff)
    return pixbuf.save_to_bufferv('png', [], [])[1]


PNG = png_bytes()


class Handler(http.server.BaseHTTPRequestHandler):
    hits: ClassVar[list] = []

    def do_GET(self):
        Handler.hits.append((self.path, self.headers.get('User-Agent')))
        if self.path.startswith('/slow'):
            time.sleep(0.6)
        if self.path.startswith(('/ok', '/slow')):
            body = PNG
        elif self.path == '/big':
            body = b'x' * (art.MAX_BYTES + 10)
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_CACHE_HOME', str(tmp_path / 'cache'))
    Handler.hits = []
    httpd = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{httpd.server_address[1]}'
    httpd.shutdown()
    httpd.server_close()


def fetched(url):
    """fetch URL and wait for its result: (returned right away, delivered path)."""
    done = threading.Event()
    result = []
    now = art.fetch(url, lambda path: result.append(path) or done.set(), deliver=lambda fn: fn())
    if now is None:
        assert done.wait(5)
    return now, (result[0] if result else None)


def test_fetches_once_then_serves_the_cache(server):
    now, path = fetched(f'{server}/ok.png')
    assert now is None and path == art.cache_path(f'{server}/ok.png')
    assert path.read_bytes() == PNG
    assert Handler.hits == [('/ok.png', 'notifyd')]          # a generic client name, nothing else
    now, _ = fetched(f'{server}/ok.png')
    assert now == path and len(Handler.hits) == 1


@pytest.mark.parametrize('name', ['/big', '/missing'])
def test_too_big_or_missing_is_nothing(server, name):
    _now, path = fetched(f'{server}{name}')
    assert path is None
    assert not art.cache_path(f'{server}{name}').exists()


def test_a_slow_server_times_out(server, monkeypatch):
    monkeypatch.setattr(art, 'TIMEOUT', 0.2)
    _now, path = fetched(f'{server}/slow')
    assert path is None


def test_one_download_per_url_at_a_time(server):
    calls = []
    done = threading.Event()
    url = f'{server}/slow2'
    assert art.fetch(url, lambda p: calls.append(p) or done.set(), deliver=lambda fn: fn()) is None
    assert art.fetch(url, lambda p: calls.append(('second', p)), deliver=lambda fn: fn()) is None
    assert done.wait(5)
    time.sleep(0.1)
    assert calls == [art.cache_path(url)]
    assert [hit[0] for hit in Handler.hits] == ['/slow2']


def test_cache_keeps_the_newest(server, monkeypatch):
    monkeypatch.setattr(art, 'KEEP', 2)
    for name in ('a', 'b', 'c'):
        fetched(f'{server}/ok-{name}')
        time.sleep(0.02)                                     # distinct mtimes
    assert sorted(p.name for p in art.cache_dir().iterdir()) == sorted(
        art.cache_path(f'{server}/ok-{name}').name for name in ('b', 'c'))


def test_only_http_urls(server):
    for url in ('file:///etc/passwd', '/tmp/x.png', 'ftp://example.org/a.png', '', None):
        assert art.fetch(url, lambda _p: pytest.fail('fetched')) is None
    assert Handler.hits == []
