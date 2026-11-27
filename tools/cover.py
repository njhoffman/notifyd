#!/usr/bin/env python3
"""cover.py PATH [SIZE]: a gradient PNG to stand in for cover art (the demo and the preview)."""
import sys

import gi

gi.require_version('GdkPixbuf', '2.0')
from gi.repository import GdkPixbuf, GLib  # noqa: E402

path = sys.argv[1]
size = int(sys.argv[2]) if len(sys.argv) > 2 else 96
data = bytes(v for y in range(size) for x in range(size)
             for v in (40 + x * 2 * 96 // size, 30 + y * 96 // size, 120 + (x + y) * 96 // size // 2))
GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(data), GdkPixbuf.Colorspace.RGB, False, 8, size, size,
                                size * 3).savev(path, 'png', [], [])
