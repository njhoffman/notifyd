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


def test_rule_buttons_run_commands_instead_of_signalling(make_manager):
    m, _s, _r, _e, closed, actions = make_manager(
        'rules:\n  - match: {app: MPD}\n    buttons: [{label: Next, run: mpc next}, {label: Love, run: love}]\n')
    nid = m.notify('MPD', summary='song', actions=['default', 'Open'])
    assert m.active[nid].actions == [('default', 'Open'), ('notifyd-button-0', 'Next'), ('notifyd-button-1', 'Love')]
    m.invoke(nid, 'notifyd-button-0')
    assert m.ran == [('button', 'mpc next', nid)]
    assert actions == []                       # the sender never asked for this action
    assert closed == [(nid, DISMISSED)]


def test_run_command_for_every_match_even_skipped(make_manager):
    m, *_ = make_manager('rules:\n  - match: {app: log-me}\n    run: logger notified\n    skip: true\n')
    nid = m.notify('log-me', summary='x')
    m.notify('other', summary='y')
    assert m.ran == [('run', 'logger notified', nid)]


def test_sounds_from_hints_and_rules(make_manager):
    m, *_ = make_manager('rules:\n  - match: {app: quiet}\n    sound: false\n'
                         '  - match: {app: loud}\n    sound: bell\n')
    m.notify('a', summary='1', hints={'sound-name': 'message-new-instant'})
    m.notify('a', summary='2', hints={'sound-file': '/x/ding.oga', 'sound-name': 'ignored'})
    m.notify('a', summary='3', hints={'sound-name': 'x', 'suppress-sound': True})
    m.notify('quiet', summary='4', hints={'sound-name': 'x'})
    m.notify('loud', summary='5', hints={'suppress-sound': True})
    m.notify('a', summary='6')
    assert m.sounds == ['message-new-instant', '/x/ding.oga', 'bell']


def test_idle_user_keeps_notifications(make_manager):
    m, s, _r, _e, closed, _a = make_manager('timeout: 5s\nidle-threshold: 1m')
    nid = m.notify('a', summary='x')
    m.idle_ms[0] = 120000                      # away for two minutes
    s.advance(60000)
    assert closed == [] and nid in m.displayed
    m.idle_ms[0] = 500                         # back
    s.advance(2000)
    assert closed == [(nid, EXPIRED)]


def test_idle_threshold_off_by_default(make_manager):
    m, s, _r, _e, closed, _a = make_manager('timeout: 5s')
    nid = m.notify('a', summary='x')
    m.idle_ms[0] = 10 ** 9
    s.advance(5000)
    assert closed == [(nid, EXPIRED)]


def test_pop_shows_a_closed_notification_again(make_manager):
    m, *_ = make_manager()
    first = m.notify('mail', summary='one', hints={'urgency': 2, 'category': 'email'})
    m.close(first, DISMISSED)
    second = m.notify('mail', summary='two')
    m.close(second, DISMISSED)
    again = m.pop()
    assert m.active[again].summary == 'two'
    older = m.pop(first)
    assert m.active[older].summary == 'one' and m.active[older].urgency_name == 'critical'
    assert m.active[older].category == 'email'
    assert m.pop(999) == 0


def test_mode_is_remembered(make_manager, tmp_path):
    state = tmp_path / 'state.json'
    m, *_ = make_manager(state_path=state)
    m.set_mode('dnd')
    again, *_ = make_manager(state_path=state)
    assert again.mode == 'dnd'
    state.write_text('not json')
    assert make_manager(state_path=state)[0].mode == 'normal'


def test_bad_buttons_and_run_are_config_errors():
    import pytest

    from notifyd.config import ConfigError
    for text in ('buttons: [Next]', 'buttons: [{label: Next}]', 'run: [a]', 'sound: 3'):
        with pytest.raises(ConfigError):
            load_config(text=text)
