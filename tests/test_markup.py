import pytest

from notifyd.markup import expand_classes, markup_error, plain_text, prepare, safe_link

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
    ('a < b <b>x</b> <3', 'a &lt; b <b>x</b> &lt;3'),         # bare <, markup kept
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


@pytest.mark.parametrize('text', [
    '', 'plain words', 'a < b and c > d', 'Tom & Jerry <b>x</b>', '<b>bold</b> &amp;', 'a<br>b',
    'see <img src="x.png" alt="pic"/>', '<span class="accent">a</span>',
])
def test_markup_error_is_empty_for_text_that_renders(text):
    assert markup_error(text, classes=CLASSES) == ''


@pytest.mark.parametrize('text', [
    '<agent-message from="a1">report</agent-message>', '<b>broken', '<foo>x</foo>',
])
def test_markup_error_names_the_problem_and_nothing_is_stripped(text):
    assert markup_error(text)
    assert markup_error(text, 'strip')
    assert prepare(text) == escape_all(text)   # shown as typed, tags and all


def test_markup_error_is_empty_in_plain_mode():
    assert markup_error('<b>broken', 'plain') == ''


def escape_all(text):
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')


@pytest.mark.parametrize('text, expected', [
    ('see <a href="https://x.org">link</a>', 'see <a href="https://x.org">link</a>'),
    ('<a href="u" title="t">L</a> <b>b</b>', '<a href="u" title="t">L</a> <b>b</b>'),
    ('<a href="https://x.org/?a=1&b=2">q</a>', '<a href="https://x.org/?a=1&amp;b=2">q</a>'),   # bare & in href
])
def test_links_are_kept_for_the_label(text, expected):
    assert prepare(text) == expected
    assert markup_error(text) == ''


@pytest.mark.parametrize('text', [
    '<a>no href</a>', '<a href="x"><a href="y">nested</a></a>', 'stray</a>', '<A HREF="u">upper</A>',
    '<a href="u" onclick="x">other attribute</a>',
])
def test_links_gtklabel_refuses_are_shown_as_typed(text):
    assert markup_error(text)
    assert prepare(text) == escape_all(text)


def test_link_text_in_strip_mode_and_plain_text():
    assert prepare('see <a href="https://x.org">link</a>', 'strip') == 'see link'
    assert plain_text('see <a href="https://x.org">link</a> &amp; more') == 'see link & more'


@pytest.mark.parametrize('uri, ok', [
    ('https://x.org', True), ('HTTP://x.org', True), ('mailto:a@b.c', True),
    ('file:///etc/passwd', False), ('javascript:alert(1)', False), ('x.org', False), ('', False), (None, False),
])
def test_only_web_and_mail_links_open(uri, ok):
    assert safe_link(uri) is ok


@pytest.mark.parametrize('text, expected', [
    ('a&nbsp;b <b>x</b>', 'a&#160;b <b>x</b>'),                 # HTML names Pango lacks become numbers
    ('wait&hellip; &mdash; &copy;', 'wait&#8230; &#8212; &#169;'),
    ('&foo; <b>y</b>', '&amp;foo; <b>y</b>'),                    # unknown names show as typed, markup kept
    ('&amp; &lt; &gt; &quot; &apos; &#65; &#x41;', '&amp; &lt; &gt; &quot; &apos; &#65; &#x41;'),
])
def test_html_entities(text, expected):
    assert prepare(text) == expected
    assert markup_error(text) == ''


def test_html_entities_in_plain_text():
    assert plain_text(prepare('a&nbsp;b&hellip;')) == 'a\u00a0b\u2026'
