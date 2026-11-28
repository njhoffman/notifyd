import json
from pathlib import Path

import pytest

from notifyd.config import ConfigError, load_config
from notifyd.model import CLOSED, DISMISSED, EXPIRED, UNDEFINED


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


def test_replaces_id_of_a_closed_notification_is_kept(make_manager):
    m, _s, renderer, events, _c, _a = make_manager()
    nid = m.notify('a', summary='one')
    other = m.notify('a', summary='other')
    m.close(nid)
    assert m.notify('a', replaces_id=nid, summary='two') == nid       # as the spec (and dunst) return it
    assert m.active[nid].summary == 'two' and m.displayed == [nid, other]
    assert renderer.calls[-1] == ('show', nid)
    assert kinds(events)[-1] == 'shown'
    assert m.notify('a', summary='new') == other + 1                  # the counter is not disturbed


def test_replaces_id_that_was_never_issued_gets_a_new_id(make_manager):
    m, *_ = make_manager()
    first = m.notify('a', summary='one')
    assert m.notify('a', replaces_id=500, summary='two') == first + 1   # 500 would collide with a later id
    assert m.notify('a', replaces_id=-3, summary='three') == first + 2


def test_replaces_id_and_stack_tag_pointing_at_different_ones(make_manager):
    m, *_ = make_manager()
    tagged = m.notify('a', summary='vol', hints={'x-dunst-stack-tag': 'vol'})
    plain = m.notify('a', summary='plain')
    assert m.notify('a', replaces_id=plain, summary='both', hints={'x-dunst-stack-tag': 'vol'}) == plain
    assert m.active[plain].summary == 'both' and m.active[tagged].summary == 'vol'
    assert sorted(m.displayed) == sorted([tagged, plain])


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


def test_forget_and_ids_continue_after_kept_history(make_manager, tmp_path):
    from notifyd.history import History
    from notifyd.manager import Manager
    path = tmp_path / 'h.jsonl'
    m, _s, _r, events, *_ = make_manager()
    m.history = History(10, path)
    for name in 'abc':
        m.close(m.notify(name, summary=name), DISMISSED)
    assert m.forget(2) == 1
    assert [e['app'] for e in m.history] == ['a', 'c']
    assert events[-1]['event'] == 'history'
    assert m.forget(99) == 0
    again = Manager(m.config, m.log, m.scheduler, history_path=path)
    assert [e['app'] for e in again.history] == ['a', 'c']
    assert again.notify('d', summary='d') == 4          # after the highest kept id
    assert again.forget() == 2 and path.read_text() == ''


def test_group_by_app_counts_and_keeps_the_latest(make_manager):
    m, _s, _r, events, *_ = make_manager('rules:\n  - match: {app: chat}\n    group-by: [app]\n')
    first = m.notify('chat', summary='alice', body='hi')
    assert m.notify('chat', summary='bob', body='yo') == first
    assert m.active[first].summary == 'bob' and m.active[first].duplicates == 2
    assert events[-1]['reason'] == 'grouped x2'
    assert m.notify('mail', summary='x') != m.notify('mail', summary='y')   # not grouped without the rule


def test_group_by_validation():
    import pytest

    from notifyd.config import ConfigError
    assert load_config(text='group-by: app').settings['group-by'] == ['app']
    with pytest.raises(ConfigError):
        load_config(text='group-by: [colour]')


def test_sender_is_kept_in_the_history(make_manager):
    manager, scheduler, *_ = make_manager('timeout: 1s')
    nid = manager.notify('Spotify', summary='song', sender={'pid': 7, 'process': 'spotify'},
                         hints={'desktop-entry': 'spotify'})
    plain = manager.notify('x', summary='no sender')
    scheduler.advance(1000)
    entries = {e['id']: e for e in manager.history}
    assert entries[nid]['sender'] == {'pid': 7, 'process': 'spotify', 'desktop_entry': 'spotify'}
    assert 'sender' not in entries[plain]


def test_invalid_markup_is_logged_as_a_warning(make_manager):
    m, _s, _r, events, _c, _a = make_manager()
    m.notify('a', summary='ok', body='<agent-message from="x">report</agent-message>')
    m.notify('a', summary='<b>fine</b>', body='a < b')
    warns = [e for e in events if e['event'] == 'warn']
    assert len(warns) == 1
    assert warns[0]['message'].startswith('#1 body is not valid markup, shown as typed: ')


def test_plain_markup_setting_logs_no_warning(make_manager):
    m, _s, _r, events, _c, _a = make_manager('markup: plain')
    m.notify('a', summary='<b>broken')
    assert 'warn' not in kinds(events)


def test_a_renderer_failure_on_show_closes_it_and_frees_the_slot(make_manager):
    m, s, renderer, events, closed, _a = make_manager('max-visible: 1\ntimeout: 5s')
    real_show = renderer.show

    def show(n):
        if n.summary == 'bad':
            raise ValueError('invalid literal for int()')
        real_show(n)
    renderer.show = show
    bad = m.notify('a', summary='bad')
    assert closed == [(bad, UNDEFINED)]
    assert m.displayed == [] and bad not in m.active and bad not in m.timers
    assert m.history.entries[-1]['id'] == bad
    assert any(e['event'] == 'error' and f'#{bad} could not be drawn (show): ValueError' in e['message']
               for e in events)
    good = m.notify('a', summary='good')                  # the slot max-visible gave the bad one is free
    assert m.displayed == [good]
    s.advance(5000)
    assert closed[-1] == (good, EXPIRED)


def test_a_renderer_failure_on_update_or_close_keeps_the_manager_consistent(make_manager):
    m, s, renderer, events, closed, _a = make_manager('timeout: 5s')
    nid = m.notify('a', summary='one')
    renderer.update = renderer.close = lambda _x: (_ for _ in ()).throw(RuntimeError('gone'))
    assert m.notify('a', replaces_id=nid, summary='two') == nid
    assert m.active[nid].summary == 'two' and nid in m.timers      # the timer restarted anyway
    s.advance(5000)
    assert closed == [(nid, EXPIRED)] and m.displayed == []
    assert [e['message'].split(':')[0] for e in events if e['event'] == 'error'] == [
        f'#{nid} could not be drawn (update)', f'#{nid} could not be drawn (close)']


SONG_RULE = 'rules:\n- match: {app: player}\n  run: echo hi\n  buttons: [{label: Skip, run: skip}]\n'


def test_held_back_notifications_and_pause_survive_a_restart(make_manager, tmp_path):
    state = tmp_path / 'state.json'
    m, _s, _r, _e, closed, _a = make_manager(SONG_RULE, state_path=state)
    m.pause(True)
    first = m.notify('player', summary='song', body='<b>artist</b>', app_icon='music', actions=['play', 'Play'],
                     hints={'category': 'x', 'image-data': (1, 1, 3, False, 8, 3, b'abc'), 'urgency': 1},
                     sender={'process': 'musicman'})
    m.notify('chat', summary='hi')
    assert m.ran == [('run', 'echo hi', first)]
    saved = json.loads(state.read_text())
    assert saved['paused'] is True and [h['summary'] for h in saved['held']] == ['song', 'hi']
    assert 'image-data' not in saved['held'][0]['hints']          # bytes are not kept
    assert saved['held'][0]['actions'] == [['play', 'Play']]       # the rule's button comes back from the rule
    received = m.active[first].received
    assert m.shutdown() == 0
    assert sorted(closed) == [(1, UNDEFINED), (2, UNDEFINED)]
    assert len(m.history) == 0                                     # not closed into history: they come back

    again, _s2, renderer, _e2, _c2, _a2 = make_manager(SONG_RULE, state_path=state)
    assert again.paused is True
    held = [again.active[nid] for nid in again.held_back]
    assert [(n.app, n.summary, n.body, n.app_icon) for n in held] == [
        ('player', 'song', '<b>artist</b>', 'music'), ('chat', 'hi', '', '')]
    assert held[0].actions == [('play', 'Play'), ('notifyd-button-0', 'Skip')]
    assert held[0].sender == {'process': 'musicman'} and held[0].received == received
    assert held[0].hints['category'] == 'x'
    assert again.ran == []                                          # run: is not repeated
    assert renderer.calls == []
    again.pause(False)
    assert len(again.displayed) == 2
    assert json.loads(state.read_text()) == {'mode': 'normal', 'paused': False, 'held': [], 'missed': 0,
                                             'paused_until': None, 'mode_until': None, 'mode_after': None}


def test_a_held_one_closed_while_paused_leaves_the_saved_queue(make_manager, tmp_path):
    state = tmp_path / 'state.json'
    m, *_ = make_manager(state_path=state)
    m.pause(True)
    nid = m.notify('a', summary='one')
    m.notify('a', summary='two')
    m.close(nid)
    assert [h['summary'] for h in json.loads(state.read_text())['held']] == ['two']


def test_a_damaged_state_file_restores_what_it_can(make_manager, tmp_path):
    state = tmp_path / 'state.json'
    state.write_text(json.dumps({'mode': 'focus', 'paused': True,
                                 'held': [{'summary': 'ok'}, 'junk', {'summary': 'bad', 'timeout': 'soon'}]}))
    m, _s, _r, events, _c, _a = make_manager(state_path=state)
    assert m.paused and m.mode == 'focus'
    assert [m.active[nid].summary for nid in m.held_back] == ['ok']
    assert any(e['event'] == 'warn' and 'could not be restored' in e['message'] for e in events)


def test_not_paused_at_shutdown_restores_nothing(make_manager, tmp_path):
    state = tmp_path / 'state.json'
    m, *_ = make_manager(state_path=state)
    m.notify('a', summary='shown')
    m.shutdown()
    again, *_ = make_manager(state_path=state)
    assert not again.paused and again.held_back == [] and again.active == {}


def image_data(width=80, height=40):
    return (width, height, width * 3, False, 8, 3, bytes(width * height * 3))


POP_RULES = 'rules:\n- match: {process: musicman}\n  run: notify-hook\n  sound: bell\n  markup: plain\n'


def test_history_keeps_icon_image_markup_and_a_thumbnail(make_manager, tmp_path):
    thumbs = tmp_path / 'thumbs'
    m, *_ = make_manager(POP_RULES, thumbs_path=thumbs)
    sender = {'process': 'musicman', 'parent': 'bash', 'desktop_entry': 'musicman'}
    a = m.notify('Spotify', summary='<b>Song</b>', app_icon='spotify', hints={'image-data': image_data()},
                 sender=sender)
    b = m.notify('chat', summary='hi', hints={'image-path': '/tmp/avatar.png'})
    m.close(a)
    m.close(b)
    first, second = m.history.entries
    assert first['app_icon'] == 'spotify' and first['markup'] == 'plain' and first['sender'] == sender
    assert Path(first['thumb']).parent == thumbs and Path(first['thumb']).name.startswith(f'{a}-')
    from gi.repository import GdkPixbuf
    pixbuf = GdkPixbuf.Pixbuf.new_from_file(first['thumb'])
    assert (pixbuf.get_width(), pixbuf.get_height()) == (64, 32)          # at most 64 px, aspect kept
    assert oct(Path(first['thumb']).stat().st_mode & 0o777) == '0o600'
    assert second['image'] == '/tmp/avatar.png' and 'thumb' not in second and 'markup' not in second


def test_pop_brings_back_icon_image_and_sender_without_run_or_sound(make_manager, tmp_path):
    m, *_ = make_manager(POP_RULES, thumbs_path=tmp_path / 'thumbs')
    nid = m.notify('Spotify', summary='Song', app_icon='spotify', hints={'image-data': image_data()},
                   sender={'process': 'musicman', 'desktop_entry': 'musicman'})
    assert m.ran == [('run', 'notify-hook', nid)] and m.sounds == ['bell']
    m.close(nid)
    again = m.pop(nid)
    n = m.active[again]
    assert n.app_icon == 'spotify' and n.hints['image-path'] == m.history.entries[0]['thumb']
    assert n.sender['process'] == 'musicman' and n.hints['desktop-entry'] == 'musicman'
    assert n.settings['markup'] == 'plain'                                # sender rules match again
    assert m.ran == [('run', 'notify-hook', nid)] and m.sounds == ['bell']  # but stay quiet


def test_history_keeps_the_title_end_for_center_cards_and_pop(make_manager):
    m, *_ = make_manager('')
    end = '<span foreground="#ff8fc7">h</span> 4.5'
    nid = m.notify('musicman', summary='Song', hints={'x-notifyd-title-end': end})
    plain = m.notify('chat', summary='hi', hints={'x-notifyd-title-end': '  '})
    m.close(nid)
    m.close(plain)
    entry, other = m.history.entries
    assert entry['title_end'] == end and 'title_end' not in other
    assert m.entry_notification(entry).title_end == end
    assert m.active[m.pop(nid)].title_end == end


def test_thumbnails_go_with_their_history_entries(make_manager, tmp_path):
    thumbs = tmp_path / 'thumbs'
    m, *_ = make_manager('history: {size: 2}', thumbs_path=thumbs)
    ids = []
    for i in range(3):
        nid = m.notify('a', summary=f's{i}', hints={'image-data': image_data(4, 4)})
        m.close(nid)
        ids.append(nid)
    kept = {e['id']: e['thumb'] for e in m.history}
    assert sorted(p.name for p in thumbs.iterdir()) == sorted(Path(t).name for t in kept.values())   # s0 fell out
    assert set(kept) == {ids[1], ids[2]}
    popped = m.pop(ids[1])
    m.close(popped)                                     # its entry points at ids[1]'s thumbnail
    assert m.history.entries[-1]['image'] == kept[ids[1]]
    assert Path(kept[ids[1]]).exists()                # kept: the popped entry still shows it
    m.forget()
    assert list(thumbs.iterdir()) == []


def test_malformed_image_data_makes_no_thumbnail(make_manager, tmp_path):
    m, *_ = make_manager(thumbs_path=tmp_path / 'thumbs')
    nid = m.notify('a', summary='s', hints={'image-data': (10, 10, 4000000, False, 8, 3, bytes(400))})
    m.close(nid)
    assert 'thumb' not in m.history.entries[-1]


def test_edge_newlines_and_spaces_are_dropped_inner_ones_kept(make_manager):
    m, *_ = make_manager()
    nid = m.notify('a', summary='\n  Title \n', body='\n\nline one\n\nline two\n\n  ')
    n = m.active[nid]
    assert n.summary == 'Title' and n.body == 'line one\n\nline two'
    assert m.active[m.notify('a', summary='\n', body=' ')].body == ''


def test_previews_have_no_side_effects(make_manager):
    from notifyd.daemon import PREVIEW_EXTRA
    m, *_ = make_manager('rules:\n- match: {app: musicman}\n  run: hook\n  sound: bell\n'
                         '  buttons: [{label: Skip, run: skip}]\n')
    nid = m.notify('musicman', summary='sample', extra={**PREVIEW_EXTRA, 'preset': 'deadd'})
    n = m.active[nid]
    assert m.ran == [] and m.sounds == [] and n.actions == []
    assert n.timeout == 15000 and n.settings['preset'] == 'deadd'
    m.close(nid)
    assert len(m.history) == 0


def test_invoking_a_key_it_never_offered_does_nothing(make_manager):
    m, _s, _r, events, closed, actions = make_manager()
    nid = m.notify('a', summary='x', actions=['play', 'Play'])
    assert m.invoke(nid, 'typo') is False
    assert actions == [] and closed == [] and nid in m.active and 'action' not in kinds(events)
    assert m.invoke(nid, 'play') is True
    assert actions == [(nid, 'play')]


def fullscreen_manager(make_manager, text='fullscreen: delay'):
    m, s, renderer, events, _closed, _a = make_manager(text)
    state = [True]
    renderer.fullscreen = lambda: state[0]
    return m, s, renderer, events, state


def test_fullscreen_delay_waits_then_shows_in_order(make_manager):
    m, s, _renderer, events, state = fullscreen_manager(make_manager)
    first = m.notify('a', summary='one')
    second = m.notify('a', summary='two')
    urgent = m.notify('a', summary='critical', hints={'urgency': 2})
    assert m.displayed == [urgent] and m.deferred == [first, second]       # critical ones always show
    assert [e.get('reason') for e in events if e['event'] == 'queued'] == ['fullscreen', 'fullscreen']
    assert {e['id']: e['state'] for e in m.listing()}[first] == 'fullscreen'
    assert m.status()['deferred'] == 2
    s.advance(3000)                                                       # still fullscreen: still waiting
    assert m.deferred == [first, second]
    state[0] = False
    s.advance(1000)
    assert m.deferred == [] and m.displayed == [second, first, urgent]   # newest on top, as usual


def test_fullscreen_show_is_the_default(make_manager):
    m, _s, _r, _e, _state = fullscreen_manager(make_manager, '')
    nid = m.notify('a', summary='one')
    assert m.displayed == [nid] and m.active[nid].fullscreen is True


def test_fullscreen_rule_and_match_field(make_manager):
    m, _s, _r, _e, state = fullscreen_manager(
        make_manager, 'rules:\n- match: {app: game-chat}\n  fullscreen: delay\n'
                      '- match: {fullscreen: true}\n  timeout: 2s\n')
    chat = m.notify('game-chat', summary='hi')
    other = m.notify('mail', summary='new')
    assert m.deferred == [chat] and m.displayed == [other]
    assert m.active[other].timeout == 2000                               # matched on fullscreen
    state[0] = False
    assert m.active[m.notify('mail', summary='later')].timeout != 2000


def test_fullscreen_then_paused_moves_to_held_back(make_manager):
    m, s, _r, _e, state = fullscreen_manager(make_manager)
    nid = m.notify('a', summary='one')
    m.pause(True)
    state[0] = False
    s.advance(1000)
    assert m.held_back == [nid] and m.deferred == [] and m.displayed == []


def test_fullscreen_deferred_ones_close_with_close_all(make_manager):
    m, s, _r, _e, state = fullscreen_manager(make_manager)
    m.notify('a', summary='one')
    assert m.close_all() == 1 and m.deferred == [] and m.active == {}
    state[0] = False
    s.advance(1000)                                                       # the pending check finds nothing


def test_fullscreen_check_failure_shows_it(make_manager):
    m, _s, renderer, events, _c, _a = make_manager('fullscreen: delay')
    renderer.fullscreen = lambda: 1 / 0
    nid = m.notify('a', summary='one')
    assert m.displayed == [nid]
    assert any(e['event'] == 'warn' and 'fullscreen check' in e['message'] for e in events)


def test_locked_holds_everything_and_unlock_shows_it(make_manager):
    m, _s, _r, events, _c, _a = make_manager()
    m.set_locked(True)
    normal = m.notify('a', summary='one')
    urgent = m.notify('a', summary='two', hints={'urgency': 2})     # critical ones too, unlike pause
    assert m.displayed == [] and m.held_back == [normal, urgent]
    assert [e.get('reason') for e in events if e['event'] == 'queued'] == ['locked', 'locked']
    m.set_locked(False)
    assert m.held_back == [] and m.displayed == [urgent, normal]
    assert [e['event'] for e in events if e['event'] in ('locked', 'unlocked')] == ['locked', 'unlocked']


def test_unlock_keeps_do_not_disturb(make_manager):
    m, *_ = make_manager()
    m.pause(True)
    m.set_locked(True)
    normal = m.notify('a', summary='one')
    urgent = m.notify('a', summary='two', hints={'urgency': 2})
    m.pause(False)                                                    # still locked: nothing shows
    assert m.displayed == []
    m.pause(True)
    m.set_locked(False)                                               # paused: only the critical one
    assert m.displayed == [urgent] and m.held_back == [normal] and m.paused
    m.pause(False)
    assert m.displayed == [normal, urgent]


def test_held_by_the_lock_at_shutdown_goes_to_history(make_manager, tmp_path):
    m, _s, _r, _e, closed, _a = make_manager(state_path=tmp_path / 'state.json')
    m.set_locked(True)
    nid = m.notify('a', summary='one')
    m.shutdown()
    assert closed == [(nid, UNDEFINED)] and m.history.entries[-1]['id'] == nid
    again, *_ = make_manager(state_path=tmp_path / 'state.json')
    assert again.active == {}


def test_ignore_close_keeps_it_open_when_the_app_closes_it(make_manager):
    m, _s, _r, events, closed, _a = make_manager('rules:\n- match: {app: chat}\n  ignore-close: true\n')
    kept = m.notify('chat', summary='new message')
    other = m.notify('mail', summary='new mail')
    assert m.sender_close(kept) is False and kept in m.displayed and closed == []
    assert [e['event'] for e in events][-1] == 'kept'
    assert m.sender_close(other) is True and closed == [(other, CLOSED)]
    assert m.close(kept) is True                                  # ctl close and the user still close it


def test_remaining_time_runs_and_freezes_while_hovered(make_manager):
    m, s, *_ = make_manager('timeout: 5s')
    nid = m.notify('a', summary='x')
    assert m.remaining(nid) == 5000
    s.advance(2000)
    assert m.remaining(nid) == 3000
    m.hold(nid)
    s.advance(10000)
    assert m.remaining(nid) == 3000
    m.release(nid)
    assert m.remaining(nid) == 3000
    never = m.notify('a', summary='y', timeout=0, extra={'timeout': 0})
    assert m.remaining(never) is None


def test_repeated_ids_keep_separate_entries_and_thumbnails(make_manager, tmp_path, monkeypatch):
    monkeypatch.setattr('notifyd.manager.time.time', lambda: 1795805359.279)   # both closes in one millisecond
    thumbs = tmp_path / 'thumbs'
    m, *_ = make_manager(thumbs_path=thumbs)
    first = m.notify('backdrop', summary='one', hints={'image-data': image_data(4, 4)})
    m.close(first)
    again = m.notify('backdrop', replaces_id=first, summary='two', hints={'image-data': image_data(4, 4)})
    assert again == first                                       # the sender reuses its id
    m.close(again)
    one, two = m.history.entries
    assert one['id'] == two['id'] and one['thumb'] != two['thumb'] and len(list(thumbs.iterdir())) == 2
    assert m.active[m.pop(entry=one)].summary == 'one'          # the entry asked for, not the newest
    assert m.forget(entry=one) == 1 and [e['summary'] for e in m.history][:1] == ['two']
    assert len(list(thumbs.iterdir())) == 1


def test_reload_restyles_open_notifications_but_keeps_their_clock(make_manager):
    m, s, _r, _e, _c, _a = make_manager('timeout: 5s\nrules:\n- match: {app: chat, summary: "/(?P<s>.*)/"}\n'
                                          '  run: hook\n  hide: [app-icon]\n  summary: "> {s}"\n')
    nid = m.notify('chat', summary='hi')
    n = m.active[nid]
    assert n.summary == '> hi' and n.settings['hide'] == ['app-icon'] and m.ran == [('run', 'hook', nid)]
    s.advance(2000)
    m.reload(load_config(text='timeout: 30s\nstyle: {background: red}\nrules:\n'
                              '- match: {app: chat, summary: "/(?P<s>.*)/"}\n  run: hook\n  hide: [time]\n'
                              '  summary: "> {s}"\n'))
    assert n.settings['hide'] == ['time'] and n.settings['style'] == {'background': 'red'}
    assert n.summary == '> hi'                                         # not rewritten twice
    assert m.ran == [('run', 'hook', nid)]                             # run: does not fire again
    assert m.remaining(nid) == 3000                                    # the clock goes on as it was


def test_a_rule_drops_actions_by_key_or_label(make_manager):
    m, *_ = make_manager('rules:\n'
                         '  - match: {app: Chromium}\n'
                         '    actions: {drop: [settings, /^mute/i]}\n'
                         '    buttons: [{label: Settings, run: "true"}]\n')
    nid = m.notify('Chromium', summary='x', actions=['default', 'Open', 'settings', 'Settings',
                                                     'mute-site', 'Mute this site', 'm', 'Mute all'])
    keys = [key for key, _label in m.active[nid].actions]
    assert keys == ['default', 'notifyd-button-0']       # by key, by /regex/ on key or label; buttons stay
    other = m.notify('Firefox', summary='y', actions=['settings', 'Settings'])
    assert [key for key, _ in m.active[other].actions] == ['settings']   # only where the rule matches
    m.invoke(nid, 'settings')                                           # a dropped action cannot be invoked
    assert nid in m.active


def test_run_on_receive_action_and_close(make_manager):
    m, scheduler, *_ = make_manager('rules:\n'
                                    '  - match: {app: chat}\n'
                                    '    run: log-it\n'
                                    '    run-on: [receive, action, close]\n'
                                    '  - match: {app: plain}\n'
                                    '    run: once\n')
    nid = m.notify('chat', summary='hi', actions=['reply', 'Reply'])
    m.invoke(nid, 'reply')                                              # an action, then it closes
    assert [what for what, _command, _id in m.ran] == ['run', 'run', 'run']
    assert m.ran_env == [{'NOTIFYD_EVENT': 'receive'},
                         {'NOTIFYD_EVENT': 'action', 'NOTIFYD_ACTION': 'reply'},
                         {'NOTIFYD_EVENT': 'close', 'NOTIFYD_CLOSE_REASON': 'dismissed'}]
    m.ran.clear(), m.ran_env.clear()
    other = m.notify('plain', summary='x', timeout=1000)
    scheduler.advance(1000)                                             # expires: no close hook by default
    assert other not in m.active
    assert m.ran == [('run', 'once', other)] and m.ran_env == [{'NOTIFYD_EVENT': 'receive'}]


def test_a_replaced_notification_runs_no_close_hook(make_manager):
    m, *_ = make_manager('rules:\n  - match: {app: vol}\n    run: hook\n    run-on: [close]\n')
    nid = m.notify('vol', summary='50%', hints={'x-dunst-stack-tag': 'v'})
    m.notify('vol', summary='60%', hints={'x-dunst-stack-tag': 'v'})   # replaces it in place
    assert m.ran == []
    m.close(nid)
    assert m.ran_env == [{'NOTIFYD_EVENT': 'close', 'NOTIFYD_CLOSE_REASON': 'closed'}]


def test_click_exec_runs_a_command_and_dismisses(make_manager):
    m, _s, _r, _e, closed, _a = make_manager("click: {middle: 'exec: copy-it --now'}\n")
    nid = m.notify('a', summary='code 123456')
    m.click(nid, 'middle')
    assert m.ran == [('click', 'copy-it --now', nid)]
    assert m.ran_env == [{'NOTIFYD_EVENT': 'click', 'NOTIFYD_BUTTON': 'middle'}]
    assert closed == [(nid, DISMISSED)]


class PendingFilters:
    """A filter runner whose commands answer when the test says: answer(i, result, error)."""

    def __init__(self):
        self.calls = []                       # (command, notification id, timeout, done)

    def __call__(self, command, n, timeout_ms, done):
        self.calls.append((command, n.id, timeout_ms, done))

    def answer(self, index, result=None, error=None):
        self.calls[index][3](result if result is not None or error else {}, error)


FILTERED = ('rules:\n'
            '  - match: {app: chat}\n'
            '    filter: rewrite-it\n'
            '    filter-timeout: 2s\n'
            '    run: hook\n')


def test_a_filter_holds_a_notification_until_it_answers(make_manager):
    filters = PendingFilters()
    m, _s, _r, _e, closed, _a = make_manager(FILTERED, filter_runner=filters)
    nid = m.notify('chat', summary='hi', body='raw')
    assert filters.calls[0][:3] == ('rewrite-it', nid, 2000)
    assert nid not in m.active and m.ran == []                       # not shown, run: not yet
    filters.answer(0, {'summary': ' Hello ', 'body': 'clean', 'urgency': 'critical', 'progress': 40,
                       'icon': 'mail-unread', 'hints': {'x-extra': 1}})
    n = m.active[nid]
    assert (n.summary, n.body, n.urgency, n.progress, n.app_icon) == ('Hello', 'clean', 2, 40, 'mail-unread')
    assert n.hints['x-extra'] == 1 and nid in m.displayed
    assert m.ran == [('run', 'hook', nid)]                            # run: sees the filtered one
    assert closed == []


def test_a_filter_can_skip_and_bad_output_shows_it_unchanged(make_manager):
    filters = PendingFilters()
    m, _s, _r, events, _c, _a = make_manager(FILTERED, filter_runner=filters)
    skipped = m.notify('chat', summary='spam')
    filters.answer(0, {'skip': True})
    assert skipped not in m.active and kinds(events)[-1] == 'suppressed'
    failed = m.notify('chat', summary='as sent')
    filters.answer(1, error='no answer in 2000 ms')
    assert m.active[failed].summary == 'as sent'
    odd = m.notify('chat', summary='odd')
    filters.answer(2, {'summary': 5, 'colour': 'red'})
    assert m.active[odd].summary == 'odd'
    warnings = [e['message'] for e in events if e['event'] == 'warn']
    assert any('no answer in 2000 ms; shown unchanged' in w for w in warnings)
    assert any('summary: expected text' in w for w in warnings) and any('colour: not a field' in w for w in warnings)


def test_a_replaced_or_closed_notification_ignores_its_late_filter(make_manager):
    filters = PendingFilters()
    m, _s, _r, _e, closed, _a = make_manager(FILTERED, filter_runner=filters)
    first = m.notify('chat', summary='one')
    second = m.notify('chat', summary='two', replaces_id=first)      # same id, its own filter
    assert second == first and len(filters.calls) == 2
    filters.answer(0, {'summary': 'stale'})                           # the older one's answer: dropped
    assert first not in m.active
    filters.answer(1, {})
    assert m.active[first].summary == 'two'
    gone = m.notify('chat', summary='closed early')
    assert m.close(gone) is True and closed == [(gone, CLOSED)]       # NotificationClosed, never shown
    filters.answer(2, {'summary': 'late'})
    assert gone not in m.active


def test_no_filter_runner_shows_it_unfiltered(make_manager):
    m, *_ = make_manager(FILTERED)
    nid = m.notify('chat', summary='direct')
    assert m.active[nid].summary == 'direct'


def test_a_timed_pause_resumes_on_its_own(make_manager):
    m, scheduler, *_ = make_manager()
    m.pause(True, 3600)
    nid = m.notify('a', summary='held')
    assert m.paused and nid in m.held_back and m.status()['paused_until'] > 0
    scheduler.advance(3599 * 1000)
    assert m.paused
    scheduler.advance(2000)
    assert not m.paused and nid in m.displayed and m.status()['paused_until'] is None
    m.pause(True, 60)
    m.pause(True)                                                     # a plain pause replaces the timed one
    scheduler.advance(120 * 1000)
    assert m.paused and m.pause_until is None


def test_a_timed_pause_and_mode_outlive_a_restart(make_manager, tmp_path):
    state = tmp_path / 'state.json'
    m, *_ = make_manager(state_path=state)
    m.pause(True, 600)
    m.set_mode('focus', 600)
    again, scheduler, *_ = make_manager(state_path=state)
    assert again.paused and again.mode == 'focus' and again.pause_until and again.mode_until
    scheduler.advance(601 * 1000)
    assert not again.paused and again.mode == 'normal'
    saved = json.loads(state.read_text())
    saved.update({'paused': True, 'paused_until': 1.0, 'mode': 'focus', 'mode_until': 1.0, 'mode_after': 'quiet'})
    state.write_text(json.dumps(saved))                               # both ended while notifyd was down
    late, *_ = make_manager(state_path=state)
    assert not late.paused and late.mode == 'quiet'


def test_a_timed_mode_goes_back_to_the_mode_before_it(make_manager):
    m, scheduler, *_ = make_manager()
    m.set_mode('work')
    m.set_mode('focus', 60)
    m.set_mode('meeting', 30)                                         # another timed one: back to work after
    scheduler.advance(31 * 1000)
    assert m.mode == 'work' and m.mode_until is None
    scheduler.advance(60 * 1000)
    assert m.mode == 'work'                                           # the first timer was replaced
    m.set_mode('focus', 60)
    m.set_mode('other')                                               # a plain switch ends the timed one
    scheduler.advance(61 * 1000)
    assert m.mode == 'other'


def test_missed_counts_what_expired_unclicked_until_the_center_opens(make_manager, tmp_path):
    state = tmp_path / 'state.json'
    m, scheduler, *_ = make_manager(state_path=state)
    m.notify('a', summary='one', timeout=1000)
    m.notify('a', summary='two', timeout=1000)
    clicked = m.notify('a', summary='three', timeout=5000)
    m.click(clicked, 'left')                                          # dismissed: seen, not missed
    scheduler.advance(1000)
    assert m.missed == 2 and m.status()['missed'] == 2
    assert make_manager(state_path=state)[0].missed == 2              # kept over a restart
    m.seen()
    assert m.missed == 0
    skipped, *_ = make_manager('rules:\n  - match: {app: noise}\n    skip: true\n')
    skipped.notify('noise', summary='x')
    assert skipped.missed == 0                                        # a skipped one was never meant to show


def test_close_by_criteria(make_manager):
    m, _s, _r, _e, closed, _a = make_manager()
    a = m.notify('Slack', summary='hi', hints={'urgency': 0})
    m.notify('Slack', summary='Build failed')
    c = m.notify('Signal', summary='hi')
    m.pause(True)
    held = m.notify('Slack', summary='later')
    assert m.close_matching({'app': 'Slack', 'urgency': 'low'}) == 1 and closed == [(a, CLOSED)]
    assert m.close_matching({'summary': '/^(build|later)/i'}) == 2
    assert set(m.active) == {c} and held not in m.held_back
    with pytest.raises(ValueError, match='unknown field'):
        m.close_matching({'colour': 'red'})
    with pytest.raises(ValueError, match='at least one'):
        m.close_matching({})
    with pytest.raises(ValueError, match='bad regex'):
        m.close_matching({'app': '/[/'})


def test_a_rule_position_makes_its_own_stack_with_its_own_room(make_manager):
    m, *_ = make_manager('max-visible: 2\n'
                         'rules:\n'
                         '  - match: {app: Volume}\n'
                         '    position: {anchor: bottom-center, margin: 80}\n')
    chat = [m.notify('chat', summary=f'c{i}', timeout=0) for i in range(3)]
    assert m.waiting == [chat[2]]                                    # the main stack is full
    volume = m.notify('Volume', summary='50%', timeout=0)
    assert volume in m.displayed                                     # its stack has room of its own
    assert m.active[volume].stack != m.active[chat[0]].stack
    second = m.notify('Volume', summary='other', timeout=0)
    third = m.notify('Volume', summary='third', timeout=0)
    assert m.waiting == [chat[2], third]
    m.close(second)                                                  # room in the volume stack only
    assert m.waiting == [chat[2]] and third in m.displayed
    m.close(chat[0])
    assert m.waiting == [] and chat[2] in m.displayed


def test_rule_positions_are_checked():
    for bad in ("position: {anchor: middle}", "position: {follow: keyboard}"):
        with pytest.raises(ConfigError):
            load_config(text=f'rules:\n  - match: {{app: x}}\n    {bad}\n')
    assert load_config(text='rules:\n  - match: {app: x}\n    position: {anchor: top-left}\n').rules


def test_inline_reply(make_manager):
    m, *_ = make_manager('rules:\n  - match: {app: chat}\n    run: hook\n    run-on: [action]\n')
    replies = []
    m.on_reply = lambda nid, text: replies.append((nid, text))
    nid = m.notify('chat', summary='Ann: lunch?', actions=['inline-reply', 'Reply', 'default', 'Open'])
    assert m.reply(nid, '   ') is False and nid in m.active                # nothing typed
    assert m.reply(nid, 'sure, 12:30') is True
    assert replies == [(nid, 'sure, 12:30')] and nid not in m.active
    assert m.ran_env == [{'NOTIFYD_EVENT': 'action', 'NOTIFYD_ACTION': 'inline-reply',
                          'NOTIFYD_REPLY': 'sure, 12:30'}]
    plain = m.notify('chat', summary='no reply here', actions=['default', 'Open'])
    assert m.reply(plain, 'x') is False
    asks = m.notify('chat', summary='ask', actions=['inline-reply', 'Reply'])
    assert m.invoke(asks, 'inline-reply') is False and asks in m.active    # no renderer to ask for text


class Clipboard:
    def __init__(self):
        self.copied = []

    def copy_text(self, text):
        self.copied.append(text)


def test_a_one_time_code_gets_a_copy_button(make_manager):
    m, _s, renderer, _e, closed, actions = make_manager('rules:\n  - match: {app: bank}\n    copy-code: false\n')
    clipboard = Clipboard()
    renderer.copy_text = clipboard.copy_text
    nid = m.notify('Signal', summary='Ann', body='<b>Your login code is 734 912</b>', actions=['default', 'Open'])
    assert m.active[nid].actions[-1] == ('notifyd-copy-code', 'Copy 734912')
    assert m.invoke(nid, 'notifyd-copy-code') is True
    assert clipboard.copied == ['734912'] and actions == []          # the sender hears nothing
    assert closed == [(nid, DISMISSED)]
    plain = m.notify('chat', summary='lunch at 1230?')
    assert m.active[plain].actions == []
    off = m.notify('bank', summary='Your code is 445566')
    assert m.active[off].actions == []                                                    # copy-code: false


def test_a_timed_pause_that_ran_out_waits_for_the_daemon_to_be_wired(tmp_path):
    import io

    from conftest import FakeScheduler, RecordingRenderer
    from notifyd.log import EventLog
    from notifyd.manager import Manager
    state = tmp_path / 'state.json'
    held = {'app': 'a', 'summary': 'held', 'body': '', 'app_icon': '', 'actions': [], 'hints': {},
            'timeout': -1, 'sender': {}, 'received': 1.0}
    state.write_text(json.dumps({'mode': 'normal', 'paused': True, 'held': [held], 'paused_until': 1.0}))
    m = Manager(load_config(text=''), EventLog(stream=io.StringIO(), color='never'), FakeScheduler(),
                state_path=state)
    assert m.paused and m.displayed == []                        # nothing shows before it is wired
    renderer, closed = RecordingRenderer(), []
    m.renderer, m.on_closed = renderer, lambda nid, reason: closed.append((nid, reason))
    m.start()
    assert not m.paused and len(m.displayed) == 1
    assert [call[0] for call in renderer.calls] == ['show']       # drawn by the real renderer


def test_a_replacement_in_its_filter_survives_the_old_one_closing(make_manager):
    filters = PendingFilters()
    m, scheduler, _r, _e, closed, _a = make_manager('rules:\n  - match: {summary: /update/}\n'
                                                    '    filter: tidy\n', filter_runner=filters)
    first = m.notify('app', summary='progress 10', timeout=1000)
    update = m.notify('app', summary='update 20', replaces_id=first)
    assert update == first and first in m.filtering
    scheduler.advance(1000)                                          # the old one expires meanwhile
    assert closed == [(first, EXPIRED)] and first in m.filtering
    filters.answer(0, {})
    assert m.active[first].summary == 'update 20' and first in m.displayed   # shown as a new one


def test_filtering_ones_count_for_replace_close_all_and_close_by_fields(make_manager):
    filters = PendingFilters()
    m, _s, _r, _e, closed, _a = make_manager(FILTERED, filter_runner=filters)
    a = m.notify('chat', summary='vol 10', hints={'x-dunst-stack-tag': 'vol'})
    b = m.notify('chat', summary='vol 20', hints={'x-dunst-stack-tag': 'vol'})   # replaces the pending one
    assert b == a and len(m.filtering) == 1
    filters.answer(0, {})                                             # the superseded answer: dropped
    filters.answer(1, {})
    assert [n.summary for n in m.active.values()] == ['vol 20']
    pending = m.notify('chat', summary='still filtering')
    assert m.close_matching({'summary': '/filtering/'}) == 1 and closed[-1] == (pending, CLOSED)
    shown = m.notify('chat', summary='shown')
    filters.answer(3, {})
    m.notify('chat', summary='its update', replaces_id=shown)        # pending, replacing an open one
    m.notify('chat', summary='not open yet')
    assert m.close_all() == 3                                        # vol 20, shown (its update goes too), not open yet
    filters.answer(4, {}), filters.answer(5, {})
    assert m.active == {} and m.filtering == {}                      # the update did not come back
