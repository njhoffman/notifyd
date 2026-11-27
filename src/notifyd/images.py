"""Images that come inside a notification (image-data hints), without GTK: checked, decoded, and
kept as small thumbnails for the history (the center and `ctl pop` show them after the sender's
data is gone)."""

import gi

gi.require_version('GdkPixbuf', '2.0')
from gi.repository import GdkPixbuf, GLib  # noqa: E402

from notifyd.files import private_dir  # noqa: E402

IMAGE_HINTS = ('image-data', 'image_data', 'icon_data')
THUMB_SIZE = 64

MAX_IMAGE_SIDE = 4096


def image_data_problem(data):
    """Why an image-data hint (width, height, rowstride, has_alpha, bits, channels, pixels) cannot be
    drawn safely, or ''. GdkPixbuf only checks the buffer against width*height*channels, so a large
    rowstride would make it read past the buffer in C (a crash, not an exception)."""
    try:
        width, height, rowstride, has_alpha, bits, channels, pixels = data
    except (TypeError, ValueError):
        return 'not (iiibiiay)'
    if not all(isinstance(v, int) for v in (width, height, rowstride, bits, channels)):
        return 'not (iiibiiay)'
    if bits != 8:
        return f'{bits} bits per sample (only 8)'
    if channels != (4 if has_alpha else 3):
        return f'{channels} channels with has_alpha={bool(has_alpha)}'
    if not (0 < width <= MAX_IMAGE_SIDE and 0 < height <= MAX_IMAGE_SIDE):
        return f'size {width}x{height} (1..{MAX_IMAGE_SIDE})'
    if rowstride < width * channels:
        return f'rowstride {rowstride} < {width * channels}'
    need = rowstride * (height - 1) + width * channels
    if not isinstance(pixels, (bytes, bytearray)) or len(pixels) < need:
        return f'{len(pixels) if isinstance(pixels, (bytes, bytearray)) else "no"} bytes of pixels, {need} needed'
    return ''


def pixbuf_from_image_data(data):
    """The pixbuf of an image-data hint; ValueError when it is malformed."""
    problem = image_data_problem(data)
    if problem:
        raise ValueError(f'image-data: {problem}')
    width, height, rowstride, has_alpha, bits, _channels, pixels = data
    return GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(pixels), GdkPixbuf.Colorspace.RGB, has_alpha,
                                           bits, width, height, rowstride)


def save_thumbnail(hints, directory, nid):
    """Save the first image-data among HINTS as DIRECTORY/NID.png (at most THUMB_SIZE px, 0600); its path,
    or None when there is none or it is malformed."""
    for hint in IMAGE_HINTS:
        data = hints.get(hint)
        if not isinstance(data, tuple):
            continue
        try:
            pixbuf = pixbuf_from_image_data(data)
            width, height = pixbuf.get_width(), pixbuf.get_height()
            if max(width, height) > THUMB_SIZE:
                factor = THUMB_SIZE / max(width, height)
                pixbuf = pixbuf.scale_simple(max(int(width * factor), 1), max(int(height * factor), 1),
                                             GdkPixbuf.InterpType.BILINEAR)
            private_dir(directory)
            path = directory / f'{nid}.png'
            tmp = directory / f'{nid}.tmp'
            pixbuf.savev(str(tmp), 'png', [], [])
            tmp.chmod(0o600)
            tmp.replace(path)
            return path
        except (ValueError, GLib.Error, OSError):
            return None
    return None


def prune_thumbnails(directory, keep_ids):
    """Remove the thumbnails in DIRECTORY whose ids are not in KEEP_IDS (forgotten, or fell out of history)."""
    try:
        files = list(directory.glob('*.png'))
    except OSError:
        return
    for path in files:
        if path.stem.isdigit() and int(path.stem) not in keep_ids:
            try:
                path.unlink()
            except OSError:
                pass
