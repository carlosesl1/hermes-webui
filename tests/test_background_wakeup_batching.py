"""Coalesce deferred process notifications instead of one response per event."""
from api import background_process as bg
from api import config
from api import routes
import pytest


@pytest.mark.parametrize('outcome', ['paused', 'server-error', 'exception'])
def test_rejected_batch_is_retained_without_retry_spin(monkeypatch, outcome):
    monkeypatch.setattr(bg, '_session_has_active_turn', lambda _: False)
    monkeypatch.setattr(config, 'DEFERRED_PROCESS_WAKEUPS', {})
    calls = []

    class InlineThread:
        def __init__(self, target, **kwargs):
            self.target = target

        def start(self):
            self.target()

    def start(*args, **kwargs):
        calls.append(args)
        if outcome == 'exception':
            raise RuntimeError('admission failed')
        return {'_status': 409 if outcome == 'paused' else 503,
                'error': 'process_wakeup_paused' if outcome == 'paused' else 'transient'}

    monkeypatch.setattr(bg.threading, 'Thread', InlineThread)
    monkeypatch.setattr(routes, 'start_session_turn', start)
    for i in range(3):
        bg.record_deferred_wakeup('test', f'proc_{i}', f'event-{i}')
    assert bg.drain_deferred_wakeups_for_session('test') == 1
    assert len(calls) == 1
    queued = bg.claim_deferred_wakeups('test')
    assert len(queued) == 1
    assert all(f'event-{i}' in queued[0]['wakeup_prompt'] for i in range(3))
    assert queued[0]['process_id'].startswith('wakeup-batch-')


def test_siblings_are_delivered_in_one_continuation(monkeypatch):
    sid = 'coalesced-test'
    prompts = [f'[IMPORTANT: Background process proc_{i} completed (exit_code=0).\nCommand: check\nOutput:\nresult-{i}]' for i in range(3)]
    monkeypatch.setattr(bg, '_session_has_active_turn', lambda _: False)
    monkeypatch.setattr(config, 'DEFERRED_PROCESS_WAKEUPS', {})
    calls = []
    monkeypatch.setattr(bg, '_start_server_side_wakeup_turn', lambda *a, **kw: calls.append((a, kw)))
    for i, prompt in enumerate(prompts):
        bg.record_deferred_wakeup(sid, f'proc_{i}', prompt)
    assert bg.drain_deferred_wakeups_for_session(sid) == 1
    assert len(calls) == 1
    body = calls[0][0][1]
    for prompt in prompts:
        assert prompt in body
    assert not bg.claim_deferred_wakeups(sid)
    assert bg.drain_deferred_wakeups_for_session(sid) == 0


def test_batch_budget_defers_whole_events_without_dropping_output(monkeypatch):
    sid = 'budget-test'
    monkeypatch.setattr(bg, '_session_has_active_turn', lambda _: False)
    monkeypatch.setattr(config, 'DEFERRED_PROCESS_WAKEUPS', {})
    calls = []
    monkeypatch.setattr(bg, '_start_server_side_wakeup_turn', lambda *a, **kw: calls.append((a, kw)))
    for i in range(40):
        bg.record_deferred_wakeup(sid, f'proc_{i}', f'event-{i}: ' + 'x' * 4096)
    assert bg.drain_deferred_wakeups_for_session(sid) == 1
    assert 1 < calls[0][0][1].count('event-') < 40
    assert len(calls[0][0][1].encode()) < 70000
    remainder = bg.claim_deferred_wakeups(sid)
    assert calls[0][0][1].count('event-') + len(remainder) == 40
    assert all(len(entry['wakeup_prompt']) > 4096 for entry in remainder)
