import pytest

from notifyd.markup import (
    action_link,
    cap_lines,
    expand_classes,
    markup_error,
    plain_text,
    prepare,
    safe_link,
    split_action_links,
)

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


@pytest.mark.parametrize('uri, key', [
    ('action:default', 'default'), ('ACTION:go', 'go'), ('action:', None), ('https://x.org', None),
    ('actions:x', None), ('', None), (None, None),
])
def test_action_links_name_an_action(uri, key):
    assert action_link(uri) == key
    assert not safe_link(uri) or key is None


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


@pytest.mark.parametrize('text, expected', [
    ('<p>one</p><p>two <b>b</b></p>', 'one\ntwo <b>b</b>'),
    ('<div class="msg">a</div>b', 'a\nb'),
    ('<P>upper</P>', 'upper'),
    ('<p>keep &amp; <i>it</i></p>\n', 'keep &amp; <i>it</i>'),
])
def test_block_tags_become_line_breaks(text, expected):
    assert prepare(text) == expected
    assert markup_error(text) == ''


def test_other_unknown_tags_still_show_as_typed():
    assert prepare('<p>x</p><pre>y</pre>') == 'x\n&lt;pre&gt;y&lt;/pre&gt;'
    assert markup_error('<p>x</p><pre>y</pre>')


@pytest.mark.parametrize('text, lines, expected', [
    ('a\nb\nc\nd', 2, 'a\nb…'),
    ('a\nb', 2, None),                                            # within the limit
    ('one long paragraph', 1, None),                               # wrapping is Pango's to cap
    ('<b>a\nb</b>\nc', 1, '<b>a…</b>'),                          # open tags are closed
    ('<span foreground="red"><i>a\nb</i></span>', 1, '<span foreground="red"><i>a…</i></span>'),
    ('<a href="u">x\ny</a>\nz', 1, '<a href="u">x…</a>'),
    ('a\nb', 0, None),
])
def test_cap_lines(text, lines, expected):
    assert cap_lines(text, lines) == expected


@pytest.mark.parametrize('markup, unwrapped, ranges', [
    # the Claude Code input notification's tag: the span keeps its colors, no link is left for GtkLabel
    ('<a href="action:default"><span foreground="#fff" background="#7aa2f7"> proj </span></a>',
     '<span foreground="#fff" background="#7aa2f7"> proj </span>', [('default', 0, 6)]),
    # byte offsets, as Pango indexes the text; http links stay links
    ('héllo <a href="action:go">wörld</a> and <a href="https://x.org">site</a> <a href=\'action:b\'>x</a>',
     'héllo wörld and <a href="https://x.org">site</a> x', [('go', 7, 13), ('b', 23, 24)]),
    ('no links', 'no links', []),
    ('<a href="https://x.org">only a web link</a>', '<a href="https://x.org">only a web link</a>', []),
    ('<b>bold <a href="action:a">and</a></b>', '<b>bold and</b>', [('a', 5, 8)]),
])
def test_split_action_links(markup, unwrapped, ranges):
    assert split_action_links(markup) == (unwrapped, ranges)


def test_split_action_links_leaves_markup_that_does_not_parse():
    broken = '<a href="action:x">text</a> <b>unclosed'
    assert split_action_links(broken) == (broken, [])


def test_a_class_can_space_out_the_lines_it_is_on():
    # wrapped tags (a span background each) otherwise sit right on top of each other
    classes = {'tag': {'background': '#7aa2f7', 'line-height': 1.3}}
    expanded = expand_classes('<span class="tag">indie</span>', classes)
    assert expanded == '<span background="#7aa2f7" line_height="1.3">indie</span>'
    assert markup_error(expanded) == ''                          # Pango takes it


@pytest.mark.parametrize('text, code', [
    ('Your verification code is 123456', '123456'),
    ('G-482913 is your Google verification code.', '482913'),
    ('Your login code: 734 912', '734912'),
    ('Use 5521 as your PIN', '5521'),
    ('Your OTP is 99887766. Do not share it.', '99887766'),
    ('security code 1234-5678', '12345678'),
    ('Code 1234, valid 5 minutes', '1234'),
    ('Meeting at 1430 in room 2020', None),           # no word that says code
    ('Order 123456 has shipped', None),
    ('Invoice 2026 total $1234.56', None),
    ('Call me at 555-1234', None),
    ('Your code expires in 10 minutes', None),        # too short
    ('PIN changed on 12/03/2026', None),              # a date
    ('Your code is 12.50 USD', None),
    ('Your one-time password: 445566', '445566'),
    ('Your 2FA code is 818181', '818181'),
    ('Security update 2024 available', None),      # the broad words count only as "... code"
    ('Downloaded 2048 tokens', None),
    ('Barcode 123456 scanned', None),               # whole words: not inside "Barcode" or "pinned"
    ('Your order 123456 has been pinned', None),
    ('Call 555-1234 to verify', None),
    ('Battery 2048 mAh, check the code', None),     # digits first need "is/as your ... code"
    ('Your OTP for txn of Rs 5000 is 482913', '482913'),                 # the code, not the amount
    ('OTP for transaction of INR 2,500.00 at AMAZON is 482913', '482913'),
    ('Your token is 482913', '482913'),
    ('Enter 482913 to verify your account', '482913'),
    ('FB-48291 is your Facebook confirmation code', '48291'),
    ('Your Amazon OTP is 123 456', '123456'),
    ('Zip code 90210', None),                       # codes of other kinds
    ('Error code 5003', None),
    ('Exit code 1, status code 1234', None),
    ('code review for PR 4821', None),
    ('can you pin the 2023 roadmap?', None),
    ('', None),
])
def test_one_time_code(text, code):
    from notifyd.markup import one_time_code
    assert one_time_code(text) == code
