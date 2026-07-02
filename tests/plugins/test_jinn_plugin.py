"""Jinn plugin tests — the consent gate is the product's trust surface.

The two acceptance criteria from mono issue #1312:
  - consent declined (or unset) ⇒ ZERO capture calls — no jinn-layer
    invocation, no pending file, nothing buffered;
  - consent accepted ⇒ capture on task completion, preview-gated first
    publish, per-task veto recorded locally.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

jinn = importlib.import_module("plugins.jinn")
consent = importlib.import_module("plugins.jinn.consent")
capture_buffer = importlib.import_module("plugins.jinn.capture_buffer")


class RunnerSpy:
    """Records every jinn-layer invocation; returns a canned success."""

    def __init__(self, code: int = 0, out: str = "ok"):
        self.calls: list[list[str]] = []
        self.code = code
        self.out = out

    def __call__(self, argv: list[str]) -> tuple[int, str]:
        self.calls.append(argv)
        return self.code, self.out


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    capture_buffer.reset()
    jinn._vetoed_tasks.clear()
    jinn._session_hint_shown.clear()
    spy = RunnerSpy()
    jinn._runner = spy
    yield spy
    jinn._runner = None


def _run_session(session_id: str = "s1", task_id: str = "t1", completed: bool = True):
    jinn._on_pre_llm_call(
        session_id=session_id,
        task_id=task_id,
        user_message="Fix the failing test suite",
        is_first_turn=True,
        model="test-model",
        platform="cli",
    )
    jinn._on_post_tool_call(
        tool_name="terminal",
        args={"command": "yarn test"},
        session_id=session_id,
        task_id=task_id,
        tool_call_id="call-1",
        result='{"output": "1 failed"}',
        duration_ms=50,
    )
    jinn._on_session_end(
        session_id=session_id, task_id=task_id, completed=completed, interrupted=False
    )


def _pending_files(tmp_home: Path) -> list[Path]:
    d = tmp_home / "jinn" / "pending"
    return sorted(d.glob("*.json")) if d.exists() else []


# ── The gate ─────────────────────────────────────────────────────────────────

def test_unset_consent_captures_nothing(isolated_home, tmp_path):
    _run_session()
    assert isolated_home.calls == []
    assert _pending_files(tmp_path) == []


def test_declined_consent_captures_nothing(isolated_home, tmp_path):
    consent.save_state(consent.DECLINED)
    _run_session()
    assert isolated_home.calls == []
    assert _pending_files(tmp_path) == []


def test_accepted_but_unpreviewed_holds_locally(isolated_home, tmp_path):
    consent.save_state(consent.ACCEPTED)
    _run_session()
    # Held for the preview-first rule: a pending file exists, nothing ran.
    assert isolated_home.calls == []
    files = _pending_files(tmp_path)
    assert len(files) == 1
    task = json.loads(files[0].read_text())
    assert task["provenance"] == "contributed"
    assert task["outcome"] == {"status": "completed", "verifiabilityTier": "user-accepted"}
    assert task["task"]["summary"] == "Fix the failing test suite"
    assert task["environment"]["harness"]["name"] == "jinn-hermes"
    assert task["steps"][0]["name"] == "tool:terminal"


def test_accepted_and_previewed_publishes(isolated_home, tmp_path):
    consent.save_state(consent.ACCEPTED, previewed=True)
    _run_session()
    assert len(isolated_home.calls) == 1
    argv = isolated_home.calls[0]
    assert argv[1] == "publish"
    assert "--veto" not in argv
    # Published pending file is cleaned up.
    assert _pending_files(tmp_path) == []


def test_veto_records_locally_and_never_publishes_content(isolated_home, tmp_path):
    consent.save_state(consent.ACCEPTED, previewed=True)
    jinn._handle_jinn(command_args="veto", session_id="s1", task_id="t1")
    _run_session()
    assert len(isolated_home.calls) == 1
    assert isolated_home.calls[0][1] == "publish"
    assert "--veto" in isolated_home.calls[0]


def test_publish_failure_retains_the_trace_locally(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    capture_buffer.reset()
    consent.save_state(consent.ACCEPTED, previewed=True)
    failing = RunnerSpy(code=1, out="anchor tx reverted")
    jinn._runner = failing
    try:
        _run_session()
    finally:
        jinn._runner = None
    assert len(failing.calls) == 1
    assert len(_pending_files(tmp_path)) == 1  # retained locally


def test_abandoned_session_is_marked_abandoned(isolated_home, tmp_path):
    consent.save_state(consent.ACCEPTED)
    jinn._on_pre_llm_call(
        session_id="s2", task_id="t2", user_message="x", is_first_turn=True, model="m"
    )
    jinn._on_post_tool_call(
        tool_name="terminal", args={}, session_id="s2", task_id="t2",
        tool_call_id="c", result="", duration_ms=1,
    )
    jinn._on_session_end(session_id="s2", task_id="t2", completed=False, interrupted=True)
    task = json.loads(_pending_files(tmp_path)[0].read_text())
    assert task["outcome"]["status"] == "abandoned"


# ── Consent flow ─────────────────────────────────────────────────────────────

def test_consent_flow_bare_enter_defaults_to_decline(isolated_home):
    answers = iter(["", "y", ""])  # explainer -> decline confirm -> node stub skip
    printed: list[str] = []
    status = consent.run_consent_flow(lambda _: next(answers), printed.append)
    assert status == consent.DECLINED
    assert consent.capture_enabled() is False
    assert any("Contribution is OFF" in line for line in printed)


def test_consent_flow_accept_requires_deliberate_confirm(isolated_home):
    answers = iter(["a", "n", "a", "y", ""])  # accept -> back out -> accept -> confirm -> skip stub
    printed: list[str] = []
    status = consent.run_consent_flow(lambda _: next(answers), printed.append)
    assert status == consent.ACCEPTED
    assert consent.capture_enabled() is True
    assert any("Nothing publishes until after you preview once" in line for line in printed)


def test_consent_state_survives_reload(isolated_home):
    consent.save_state(consent.ACCEPTED)
    assert consent.load_state()["status"] == consent.ACCEPTED
    consent.mark_previewed()
    state = consent.load_state()
    assert state["previewed"] is True
    assert state["status"] == consent.ACCEPTED


# ── Slash surface ────────────────────────────────────────────────────────────

def test_status_states_capture_off_by_default(isolated_home):
    out = jinn._handle_jinn(command_args="status")
    assert "consent: unset" in out
    assert "capture: OFF" in out


def test_preview_marks_previewed_and_unlocks_publish(isolated_home, tmp_path):
    consent.save_state(consent.ACCEPTED)
    _run_session()  # held pending
    out = jinn._handle_jinn(command_args="preview")
    assert "preview recorded" in out
    assert consent.load_state()["previewed"] is True
    # Preview invoked jinn-layer capture preview on the pending file.
    assert isolated_home.calls[0][1:3] == ["capture", "preview"]


def test_corpus_command_delegates_to_layer(isolated_home):
    out = jinn._handle_corpus(command_args="prediction")
    assert out == "ok"
    assert isolated_home.calls[0][1:4] == ["corpus", "search", "prediction"]
