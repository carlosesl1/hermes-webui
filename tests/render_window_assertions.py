"""Strict transcript equality plus the additive render-only paging marker."""
from api.render_payload import _paging_identity


def assert_render_window_matches(actual, expected):
    assert len(actual) == len(expected)
    for index, (row, original) in enumerate(zip(actual, expected, strict=True)):
        if index == 0 and isinstance(original, dict) and original.get('role'):
            assert row.get('_paging_identity') == _paging_identity(original)
            assert {key: value for key, value in row.items() if key != '_paging_identity'} == original
            assert '_paging_identity' not in original, 'canonical input was annotated'
        else:
            assert row == original
