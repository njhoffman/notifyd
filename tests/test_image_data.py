"""image-data hints: checked before GdkPixbuf reads them (a bad rowstride crashed the daemon)."""
import pytest

from notifyd.render.gtk import MAX_IMAGE_SIDE, image_data_problem, pixbuf_from_data


def rgb(width, height, rowstride=None, alpha=False, extra=0):
    channels = 4 if alpha else 3
    rowstride = rowstride or width * channels
    return (width, height, rowstride, alpha, 8, channels, bytes(rowstride * (height - 1) + width * channels + extra))


@pytest.mark.parametrize('data', [
    rgb(10, 10), rgb(10, 10, alpha=True), rgb(10, 10, rowstride=32), rgb(1, 1), rgb(7, 3, rowstride=24),
])
def test_well_formed_image_data_draws(data):
    assert image_data_problem(data) == ''
    pixbuf = pixbuf_from_data(data, 64)
    assert (pixbuf.get_width(), pixbuf.get_height()) == (data[0], data[1])


@pytest.mark.parametrize('data, problem', [
    ((10, 10, 4000000, False, 8, 3, bytes(400)), 'bytes of pixels'),     # the crash: rowstride past the buffer
    ((*rgb(10, 10)[:6], bytes(299)), 'bytes of pixels'),               # one byte short
    ((10, 10, 20, False, 8, 3, bytes(1000)), 'rowstride 20 < 30'),
    ((10, 10, 30, False, 16, 3, bytes(1000)), 'bits'),
    ((10, 10, 40, True, 8, 3, bytes(1000)), 'channels'),
    ((10, 10, 30, False, 8, 4, bytes(1000)), 'channels'),
    ((0, 10, 30, False, 8, 3, bytes(1000)), 'size'),
    ((10, -1, 30, False, 8, 3, bytes(1000)), 'size'),
    ((MAX_IMAGE_SIDE + 1, 1, (MAX_IMAGE_SIDE + 1) * 3, False, 8, 3, bytes(1)), 'size'),
    ((10, 10, 30, False, 8, 3), 'not (iiibiiay)'),
    (('10', 10, 30, False, 8, 3, bytes(1000)), 'not (iiibiiay)'),
    ((10, 10, 30, False, 8, 3, None), 'no bytes'),
])
def test_malformed_image_data_is_refused_before_gdkpixbuf(data, problem):
    assert problem in image_data_problem(data)
    with pytest.raises(ValueError, match='image-data'):
        pixbuf_from_data(data, 0)
