"""Retry batches remain indivisible when overflow/new completions are queued."""
import pytest

from api import background_process as bg
from api import config, routes
from api.background_batch import MAX_BATCH_EVENTS, coalesce_wakeup_entries


def _event(index):
    return {'process_id': f'proc_{index}', 'wakeup_prompt': f'event-{index:03d}'}


def _batch(start=0):
    entries = [_event(i) for i in range(start, start + MAX_BATCH_EVENTS)]
    prompt, identity, overflow = coalesce_wakeup_entries(entries)
    assert not overflow
    return {'process_id': identity, 'wakeup_prompt': prompt}


@pytest.mark.parametrize('position', ['first', 'middle', 'last', 'siblings'])
def test_retry_batches_are_fifo_barriers_with_stable_identity(position):
    batch = _batch()
    before, after = _event(100), _event(101)
    entries = {
        'first': [batch, after],
        'middle': [before, batch, after],
        'last': [before, after, batch],
        'siblings': [batch, _batch(20)],
    }[position]
    pair_prompt, pair_id, _ = coalesce_wakeup_entries([before, after])
    expected = {
        'first': [batch, after],
        'middle': [before, batch, after],
        'last': [{'wakeup_prompt': pair_prompt, 'process_id': pair_id}, batch],
        'siblings': entries,
    }[position]
    original = [entry.copy() for entry in entries]
    dispatched = []
    remaining = entries
    while remaining:
        prompt, identity, rest = coalesce_wakeup_entries(remaining)
        assert len(rest) < len(remaining)
        dispatched.append({'wakeup_prompt': prompt, 'process_id': identity})
        remaining = rest
    assert dispatched == expected
    assert entries == original
    for entry in dispatched:
        assert entry['wakeup_prompt'].count('[BACKGROUND UPDATES]') <= 1
        assert entry['wakeup_prompt'].count('event-') <= MAX_BATCH_EVENTS


def test_batch_like_plain_text_is_not_retry_provenance():
    entries = [_event(100), _event(101)]
    entries[0]['wakeup_prompt'] = '[BACKGROUND UPDATES]\nplain tool output'
    prompt, identity, overflow = coalesce_wakeup_entries(entries)
    assert not overflow
    assert identity.startswith('wakeup-batch-')
    assert all(entry['wakeup_prompt'] in prompt for entry in entries)


@pytest.mark.parametrize('outcome', ['busy', 'paused', 'server-error', 'exception'])
def test_rejected_full_batch_and_overflow_are_delivered_separately(monkeypatch, outcome):
    sid = 'retry-batch-test'
    monkeypatch.setattr(config, 'DEFERRED_PROCESS_WAKEUPS', {})
    monkeypatch.setattr(bg, '_session_has_active_turn', lambda _: False)
    attempts = []
    rejected = True

    class InlineThread:
        def __init__(self, target, **kwargs):
            self.target = target

        def start(self):
            self.target()

    def start(session_id, prompt, **kwargs):
        assert session_id == sid
        attempts.append(prompt)
        if rejected:
            if outcome == 'exception':
                raise RuntimeError('fixture: admission failed')
            return {'_status': 503 if outcome == 'server-error' else 409,
                    'error': 'process_wakeup_paused' if outcome == 'paused' else 'busy'}
        return {'_status': 200, 'stream_id': 'accepted-fixture'}

    monkeypatch.setattr(bg.threading, 'Thread', InlineThread)
    monkeypatch.setattr(routes, 'start_session_turn', start)
    entries = [_event(i) for i in range(MAX_BATCH_EVENTS + 1)]
    for entry in entries:
        bg.record_deferred_wakeup(sid, entry['process_id'], entry['wakeup_prompt'])
    assert bg.drain_deferred_wakeups_for_session(sid) == 1
    first_prompt = attempts[0]
    batch = config.DEFERRED_PROCESS_WAKEUPS[sid][-1].copy()
    assert batch['wakeup_prompt'] == first_prompt
    # Overflow is already queued when rejected admission appends the retry.
    assert config.DEFERRED_PROCESS_WAKEUPS[sid] == [entries[-1], batch]
    rejected = False
    assert bg.drain_deferred_wakeups_for_session(sid) == 1
    assert attempts[-1] == entries[-1]['wakeup_prompt']
    assert config.DEFERRED_PROCESS_WAKEUPS[sid] == [batch]
    # Another rejection must keep the exact batch identity/payload without growth.
    rejected = True
    assert bg.drain_deferred_wakeups_for_session(sid) == 1
    assert attempts[-1] == first_prompt
    assert config.DEFERRED_PROCESS_WAKEUPS[sid] == [batch]
    rejected = False
    assert bg.drain_deferred_wakeups_for_session(sid) == 1
    assert attempts[-1] == first_prompt
    assert not bg.claim_deferred_wakeups(sid)
    assert bg.drain_deferred_wakeups_for_session(sid) == 0
    accepted = attempts[1] + '\n' + attempts[-1]
    assert all(accepted.count(entry['wakeup_prompt']) == 1 for entry in entries)
