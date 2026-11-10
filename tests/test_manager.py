from notifyd.config import load_config
from notifyd.model import CLOSED, DISMISSED, EXPIRED


def kinds(events):
    return [e['event'] for e in events]


def test_new_notifications_get_increasing_ids_and_show(make_manager):
    m, _s, renderer, events, _c, _a = make_manager()
    first = m.notify('a', summary='one')
    second = m.notify('a', summary='two')
    assert (first, second) == (1, 2)
    assert renderer.calls == [('show', 1), ('show', 2)]
    assert m.displayed == [2, 1]   # newest first
    assert kinds(events) == ['shown', 'shown']


def test_expires_after_its_timeout(make_manager):
    m, s, renderer, events, closed, _a = make_manager('timeout: 5s')
    nid = m.notify('a', summary='x')
    s.advance(4999)
    assert m.displayed == [nid]
    s.advance(1)
    assert m.displayed == []
    assert closed == [(nid, EXPIRED)]
    assert ('close', nid) in renderer.calls
    assert kinds(events)[-1] == 'expired'


def test_replaces_id_keeps_the_id_and_restarts_the_clock(make_manager):
    m, s, renderer, events, closed, _a = make_manager('timeout: 5s')
    nid = m.notify('a', summary='one')
    s.advance(4000)
    again = m.notify('a', replaces_id=nid, summary='two')
    assert again == nid
    assert renderer.calls[-1] == ('update', nid)
    assert m.active[nid].summary == 'two'
    s.advance(4000)
    assert closed == []           # the replacement got a fresh 5s
    s.advance(1000)
    assert closed == [(nid, EXPIRED)]
    assert kinds(events) == ['shown', 'replaced', 'expired']


def test_replaces_id_of_a_closed_notification_gets_a_new_id(make_manager):
    m, *_ = make_manager()
    nid = m.notify('a', summary='one')
    m.close(nid)
    assert m.notify('a', replaces_id=nid, summary='two') != nid


def test_stack_tag_replaces_within_the_same_app(make_manager):
    m, *_ = make_manager()
    vol = m.notify('Volume', summary='10%', hints={'x-dunst-stack-tag': 'vol'})
    assert m.notify('Volume', summary='20%', hints={'x-dunst-stack-tag': 'vol'}) == vol
    assert m.notify('Volume', summary='30%', hints={'x-canonical-private-synchronous': 'vol'}) == vol
    assert m.notify('Other', summary='x', hints={'x-dunst-stack-tag': 'vol'}) != vol
    assert m.active[vol].summary == '30%'


def test_rule_timeout_overrides_sender(make_manager):
    m, s, _r, _e, closed, _a = make_manager('rules:\n  - match: {app: MPD}\n    timeout: 12s\n')
    nid = m.notify('MPD', summary='song', timeout=5000)
    s.advance(11000)
    assert closed == []
    s.advance(1000)
    assert closed == [(nid, EXPIRED)]


def test_never_expiring(make_manager):
    m, s, _r, _e, closed, _a = make_manager()
    m.notify('a', summary='sticky', timeout=0)
    m.notify('a', summary='critical', hints={'urgency': 2})
    s.advance(10 ** 9)
    assert closed == []


def test_hover_holds_the_clock_and_leaving_resumes_it(make_manager):
    m, s, _r, _e, closed, _a = make_manager('timeout: 5s')
    nid = m.notify('a', summary='x')
    s.advance(3000)
    m.hold(nid)
    s.advance(60000)
    assert closed == []
    m.release(nid)
    s.advance(1999)
    assert closed == []
    s.advance(1)
    assert closed == [(nid, EXPIRED)]


def test_leaving_right_before_expiry_still_gives_a_second(make_manager):
    m, s, _r, _e, closed, _a = make_manager('timeout: 5s')
    nid = m.notify('a', summary='x')
    s.advance(4990)
    m.hold(nid)
    m.release(nid)
    s.advance(999)
    assert closed == []
    s.advance(1)
    assert closed == [(nid, EXPIRED)]


def test_waiting_queue_when_the_screen_is_full(make_manager):
    m, s, _renderer, events, _c, _a = make_manager('max-visible: 2\ntimeout: 5s')
    a = m.notify('x', summary='a')
    b = m.notify('x', summary='b')
    c = m.notify('x', summary='c')
    assert m.displayed == [b, a] and m.waiting == [c]
    assert kinds(events)[-1] == 'waiting'
    m.close(a, DISMISSED)
    assert m.displayed == [c, b] and m.waiting == []
    s.advance(5000)    # c's clock started when it was shown, with b's
    assert m.displayed == []


def test_close_reasons_and_signals(make_manager):
    m, _s, _r, events, closed, _a = make_manager()
    nid = m.notify('a', summary='x')
    assert m.close(nid, CLOSED) is True
    assert m.close(nid, CLOSED) is False      # already gone
    assert closed == [(nid, CLOSED)]
    assert kinds(events)[-1] == 'closed'


def test_actions_invoke_and_close_unless_resident(make_manager):
    m, _s, _r, _e, closed, actions = make_manager()
    nid = m.notify('displayd', summary='x', actions=['change', 'Change layout'])
    m.invoke(nid, 'change')
    assert actions == [(nid, 'change')]
    assert closed == [(nid, DISMISSED)]
    resident = m.notify('a', summary='y', actions=['go', 'Go'], hints={'resident': True})
    m.invoke(resident, 'go')
    assert resident in m.active


def test_clicks_follow_config(make_manager):
    m, _s, _r, _e, closed, actions = make_manager()
    plain = m.notify('a', summary='x')
    m.click(plain, 'right')                    # default-action without actions: dismiss
    assert closed == [(plain, DISMISSED)]
    with_default = m.notify('a', summary='y', actions=['default', 'Open', 'other', 'Other'])
    m.click(with_default, 'right')
    assert actions == [(with_default, 'default')]
    one, two = m.notify('a', summary='1'), m.notify('a', summary='2')
    m.click(one, 'middle')                     # dismiss-all
    assert m.displayed == [] and two not in m.active


def test_skip_is_logged_kept_in_history_and_closed_after_reply(make_manager):
    m, s, renderer, events, closed, _a = make_manager('rules:\n  - match: {app: spam}\n    skip: true\n')
    nid = m.notify('spam', summary='buy')
    assert renderer.calls == [] and nid not in m.active
    assert kinds(events) == ['suppressed']
    assert closed == []
    s.advance(0)
    assert closed == [(nid, EXPIRED)]
    assert m.history[-1]['closed'] == 'suppressed'


def test_pause_holds_back_all_but_critical_and_replays(make_manager):
    m, _s, _renderer, events, _c, _a = make_manager()
    m.pause(True)
    low = m.notify('a', summary='later')
    critical = m.notify('a', summary='now', hints={'urgency': 2})
    assert m.displayed == [critical]
    assert m.held_back == [low]
    m.pause(False)
    assert m.displayed == [low, critical]
    assert 'queued' in kinds(events) and 'resumed' in kinds(events)


def test_history_keeps_closed_notifications_except_transient_and_opted_out(make_manager):
    m, *_ = make_manager('history: {size: 2}\nrules:\n  - match: {app: secret}\n    history: {keep: false}\n')
    for name in ('a', 'b', 'c'):
        m.close(m.notify(name, summary=name), DISMISSED)
    m.close(m.notify('t', summary='t', hints={'transient': True}))
    m.close(m.notify('secret', summary='s'))
    assert [e['app'] for e in m.history] == ['b', 'c']
    assert m.history[-1]['closed'] == 'dismissed'


def test_reload_swaps_config_for_new_notifications(make_manager):
    m, *_ = make_manager('timeout: 5s')
    m.reload(load_config(text='timeout: 9s\nhistory: {size: 3}'))
    assert m.active.get(m.notify('a', summary='x')).timeout == 9000
    assert m.history.maxlen == 3


def test_close_all_counts(make_manager):
    m, *_ = make_manager('max-visible: 1')
    for index in range(3):
        m.notify('a', summary=f'x{index}')
    assert m.close_all() == 3
    assert m.active == {}


def test_identical_notifications_stack_with_a_count(make_manager):
    m, _s, _renderer, events, _c, _a = make_manager()
    first = m.notify('mail', summary='New mail', body='from bob')
    assert m.notify('mail', summary='New mail', body='from bob') == first
    assert m.notify('mail', summary='New mail', body='from bob') == first
    assert m.active[first].duplicates == 3
    assert events[-1]['event'] == 'replaced' and events[-1]['reason'] == 'duplicate x3'
    assert m.notify('mail', summary='New mail', body='from alice') != first
    assert m.notify('mail', summary='New mail', body='from bob', hints={'urgency': 2}) != first


def test_duplicate_stacking_can_be_turned_off(make_manager):
    m, *_ = make_manager('stack-duplicates: false')
    assert m.notify('a', summary='x') != m.notify('a', summary='x')


def test_modes_are_matched_by_rules(make_manager):
    m, _s, _renderer, events, _c, _a = make_manager(
        'rules:\n  - match: {mode: dnd, urgency: [low, normal]}\n    skip: true\n')
    assert m.notify('a', summary='before') in m.active
    assert m.set_mode('dnd') == 'dnd'
    hidden = m.notify('a', summary='during')
    assert hidden not in m.active and events[-1]['event'] == 'suppressed'
    assert m.notify('a', summary='urgent', hints={'urgency': 2}) in m.active
    m.set_mode('normal')
    assert m.notify('a', summary='after') in m.active
    assert [e['message'] for e in events if e['event'] == 'mode'] == ['dnd', 'normal']
    assert m.status()['mode'] == 'normal'


def test_history_persists_across_managers(make_manager, tmp_path):
    from notifyd.history import History
    from notifyd.manager import Manager
    path = tmp_path / 'state' / 'history.jsonl'
    m, *_ = make_manager('history: {size: 3}')
    m.history = History(3, path)
    for name in 'abcdefgh':
        m.close(m.notify(name, summary=name), DISMISSED)
    again = Manager(m.config, m.log, m.scheduler, history_path=path)
    assert [e['app'] for e in again.history] == ['f', 'g', 'h']
    assert len(path.read_text().splitlines()) <= 6    # compacted, never more than twice the size


def test_history_file_survives_a_torn_line(tmp_path):
    from notifyd.history import History
    path = tmp_path / 'h.jsonl'
    path.write_text('{"app": "a"}\n{"app": "b"\n')
    assert [e['app'] for e in History(10, path)] == ['a']


def test_listing_shows_open_notifications(make_manager):
    m, *_ = make_manager('max-visible: 1')
    one = m.notify('a', summary='one')
    two = m.notify('a', summary='two')
    assert [(e['id'], e['state']) for e in m.listing()] == [(one, 'shown'), (two, 'waiting')]
