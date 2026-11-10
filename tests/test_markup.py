import pytest

from notifyd.markup import expand_classes, plain_text, prepare

CLASSES = {'accent': {'foreground': '#7aa2f7', 'weight': 'bold'}, 'muted': {'alpha': '60%'},
           'odd': {'onclick': 'x', 'font-size': '10pt'}}


def test_valid_markup_is_kept():
    text = '<b>Artist</b> <span foreground="#9ece6a" background="#000">tag</span>'
    assert prepare(text) == text


def test_classes_expand_to_span_attributes():
    assert expand_classes('<span class="accent">a</span>', CLASSES) == \
        '<span foreground="#7aa2f7" weight="bold">a</span>'
    assert expand_classes("<span class='accent muted' size='large'>a</span>", CLASSES) == \
        '<span foreground="#7aa2f7" weight="bold" alpha="60%" size=\'large\'>a</span>'


def test_unknown_class_and_unsafe_attributes_are_dropped():
    assert expand_classes('<span class="nope">a</span>', CLASSES) == '<span>a</span>'
    assert expand_classes('<span class="odd">a</span>', CLASSES) == '<span font_size="10pt">a</span>'


@pytest.mark.parametrize('text, expected', [
    ('Tom & Jerry <b>x</b>', 'Tom &amp; Jerry <b>x</b>'),   # bare ampersand fixed, markup kept
    ('<b>broken', '&lt;b&gt;broken'),                     # unparseable: shown as typed
    ('a<br>b<br/>c', 'a\nb\nc'),
    ('see <img src="x.png" alt="pic"/> here', 'see pic here'),
    ('&amp; &lt; &#65;', '&amp; &lt; &#65;'),
])
def test_full_mode_fallbacks(text, expected):
    assert prepare(text) == expected


def test_strip_mode_keeps_text_only():
    assert prepare('<b>a</b> &amp; <i>b</i>', 'strip') == 'a &amp; b'


def test_plain_mode_shows_markup_as_typed():
    assert prepare('<b>a</b>', 'plain') == '&lt;b&gt;a&lt;/b&gt;'


def test_plain_text_handles_invalid_markup():
    assert plain_text('<b>a</b> &amp; b') == 'a & b'
    assert plain_text('<span class="x">a</span> &amp; <b>b') == 'a & b'
