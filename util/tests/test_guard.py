"""Tests for util/guard.py — freshness tracking for pipeline outputs."""
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from util import guard


def _touch(path: Path, content: str = 'x', mtime: float = None) -> None:
    path.write_text(content)
    if mtime is not None:
        os.utime(path, (mtime, mtime))


# ── check_freshness: missing output ────────────────────────────────────────

def test_missing_output_is_not_fresh(tmp_path):
    out = tmp_path / 'out.parquet'
    fr = guard.check_freshness(out)
    assert fr.fresh is False
    assert 'does not exist' in fr.reason


# ── check_freshness: no guard record (mtime fallback) ─────────────────────

def test_no_guard_record_fresh_when_output_newest(tmp_path):
    inp = tmp_path / 'in.parquet'
    out = tmp_path / 'out.parquet'
    _touch(inp, mtime=100)
    _touch(out, mtime=200)
    fr = guard.check_freshness(out, inp)
    assert fr.fresh is True
    assert 'no guard record' in fr.reason


def test_no_guard_record_stale_when_input_newer(tmp_path):
    inp = tmp_path / 'in.parquet'
    out = tmp_path / 'out.parquet'
    _touch(out, mtime=100)
    _touch(inp, mtime=200)
    fr = guard.check_freshness(out, inp)
    assert fr.fresh is False
    assert str(inp) in fr.reason


def test_no_guard_record_stale_when_script_newer(tmp_path):
    script = tmp_path / 'build.py'
    out = tmp_path / 'out.parquet'
    _touch(out, mtime=100)
    _touch(script, mtime=200)
    fr = guard.check_freshness(out, script=str(script))
    assert fr.fresh is False
    assert 'producing script' in fr.reason


# ── record_build + check_freshness round trip ──────────────────────────────

def test_record_then_check_is_fresh(tmp_path):
    inp = tmp_path / 'in.parquet'
    out = tmp_path / 'out.parquet'
    script = tmp_path / 'build.py'
    _touch(inp)
    _touch(script)
    _touch(out)

    guard.record_build(out, inp, script=str(script), build_seconds=42.0)
    fr = guard.check_freshness(out, inp, script=str(script))
    assert fr.fresh is True
    assert fr.last_build_seconds == 42.0


def test_input_modified_after_record_is_stale(tmp_path):
    inp = tmp_path / 'in.parquet'
    out = tmp_path / 'out.parquet'
    _touch(inp, content='v1')
    _touch(out)
    guard.record_build(out, inp)

    time.sleep(0.01)
    _touch(inp, content='v2-different-size')  # mtime AND size change
    fr = guard.check_freshness(out, inp)
    assert fr.fresh is False
    assert 'changed since build' in fr.reason


def test_script_modified_after_record_is_stale(tmp_path):
    inp = tmp_path / 'in.parquet'
    out = tmp_path / 'out.parquet'
    script = tmp_path / 'build.py'
    _touch(inp)
    _touch(out)
    _touch(script, mtime=100)
    guard.record_build(out, inp, script=str(script))

    os.utime(script, (200, 200))
    fr = guard.check_freshness(out, inp, script=str(script))
    assert fr.fresh is False
    assert 'modified since build' in fr.reason


def test_new_input_not_in_guard_is_stale(tmp_path):
    inp1 = tmp_path / 'in1.parquet'
    inp2 = tmp_path / 'in2.parquet'
    out = tmp_path / 'out.parquet'
    _touch(inp1)
    _touch(inp2)
    _touch(out)
    guard.record_build(out, inp1)  # only inp1 recorded

    fr = guard.check_freshness(out, inp1, inp2)  # now checked against both
    assert fr.fresh is False
    assert 'new dependency' in fr.reason


def test_unrelated_input_added_later_does_not_affect_freshness(tmp_path):
    """Regression: a new snapshot file appearing shouldn't matter unless declared as an input."""
    inp = tmp_path / 'in.parquet'
    out = tmp_path / 'out.parquet'
    _touch(inp)
    _touch(out)
    guard.record_build(out, inp)

    (tmp_path / 'unrelated.parquet').write_text('new file')
    fr = guard.check_freshness(out, inp)
    assert fr.fresh is True


# ── glob expansion ──────────────────────────────────────────────────────────

def test_glob_input_expands_to_matching_files(tmp_path):
    (tmp_path / 'shard1.parquet').write_text('a')
    (tmp_path / 'shard2.parquet').write_text('b')
    out = tmp_path / 'out.parquet'
    _touch(out)

    pattern = str(tmp_path / 'shard*.parquet')
    guard.record_build(out, pattern)
    import json
    manifest = json.loads(guard._guard_path(out).read_text())
    manifest_inputs = {Path(i['path']).name for i in manifest['inputs']}
    assert manifest_inputs == {'shard1.parquet', 'shard2.parquet'}

    fr = guard.check_freshness(out, pattern)
    assert fr.fresh is True

    time.sleep(0.01)
    (tmp_path / 'shard1.parquet').write_text('a-modified')
    fr = guard.check_freshness(out, pattern)
    assert fr.fresh is False


# ── ensure_fresh ─────────────────────────────────────────────────────────────

def test_ensure_fresh_returns_true_when_output_missing(tmp_path, capsys):
    out = tmp_path / 'out.parquet'
    assert guard.ensure_fresh(out) is True


def test_ensure_fresh_returns_false_when_fresh(tmp_path):
    inp = tmp_path / 'in.parquet'
    out = tmp_path / 'out.parquet'
    _touch(inp)
    _touch(out)
    guard.record_build(out, inp)
    assert guard.ensure_fresh(out, inp) is False


def test_ensure_fresh_auto_yes_skips_prompt(tmp_path, monkeypatch):
    inp = tmp_path / 'in.parquet'
    out = tmp_path / 'out.parquet'
    _touch(out, mtime=100)
    _touch(inp, mtime=200)  # stale

    def _boom(*a, **k):
        raise AssertionError("input() should not be called when auto_yes=True")
    monkeypatch.setattr('builtins.input', _boom)

    assert guard.ensure_fresh(out, inp, auto_yes=True) is True


def test_ensure_fresh_prompts_when_stale_and_expensive(tmp_path, monkeypatch):
    inp = tmp_path / 'in.parquet'
    out = tmp_path / 'out.parquet'
    _touch(inp)
    _touch(out)
    guard.record_build(out, inp, build_seconds=999.0)  # expensive

    time.sleep(0.01)
    _touch(inp, content='changed')  # now stale

    monkeypatch.setattr('builtins.input', lambda prompt: 'n')
    assert guard.ensure_fresh(out, inp, prompt_after_seconds=180) is False

    monkeypatch.setattr('builtins.input', lambda prompt: 'y')
    assert guard.ensure_fresh(out, inp, prompt_after_seconds=180) is True


def test_ensure_fresh_no_prompt_when_stale_and_cheap(tmp_path, monkeypatch):
    inp = tmp_path / 'in.parquet'
    out = tmp_path / 'out.parquet'
    _touch(inp)
    _touch(out)
    guard.record_build(out, inp, build_seconds=1.0)  # cheap

    time.sleep(0.01)
    _touch(inp, content='changed')

    def _boom(*a, **k):
        raise AssertionError("input() should not be called for a cheap rebuild")
    monkeypatch.setattr('builtins.input', _boom)

    assert guard.ensure_fresh(out, inp, prompt_after_seconds=180) is True


def test_ensure_fresh_eof_on_input_defaults_to_no(tmp_path, monkeypatch):
    """Unattended/non-interactive runs shouldn't crash on a stale+expensive prompt."""
    inp = tmp_path / 'in.parquet'
    out = tmp_path / 'out.parquet'
    _touch(inp)
    _touch(out)
    guard.record_build(out, inp, build_seconds=999.0)

    time.sleep(0.01)
    _touch(inp, content='changed')

    def _eof(*a, **k):
        raise EOFError
    monkeypatch.setattr('builtins.input', _eof)

    assert guard.ensure_fresh(out, inp, prompt_after_seconds=180) is False
