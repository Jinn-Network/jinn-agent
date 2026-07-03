"""``bin/jinn-agent`` must expose the REAL CLI surface, not the bare TUI.

Regression for the second cold-clone dogfood run (2026-07-03): the
entrypoint exec'd ``cli.py`` directly, which only carries the interactive
TUI's argument parser. Every subcommand (``setup``, ``status``, ``doctor``)
and the non-interactive single-query flags the harness spawn pattern
depends on (``chat -q <prompt> -Q --yolo``) live in the ``hermes`` console
script (``hermes_cli.main:main``). On a cold install:

  bin/jinn-agent chat -q "..." -Q   ->  ERROR: Could not consume arg: -Q
  bin/jinn-agent setup              ->  unreachable (new users cannot
                                        configure a model; first query dies
                                        with "HTTP 400: No models provided")

Mono issue: Jinn-Network/mono#1361.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINT = REPO_ROOT / "bin" / "jinn-agent"


def _console_script() -> Path | None:
    """The hermes console script of whichever venv this checkout has."""
    for env_dir in ("venv", ".venv"):
        candidate = REPO_ROOT / env_dir / "bin" / "hermes"
        if candidate.exists():
            return candidate
    return None


def test_entrypoint_execs_the_console_script_not_the_bare_tui():
    script = ENTRYPOINT.read_text(encoding="utf-8")
    tail = script.rsplit("PY" + "EOF", 1)[1]  # after the config-ensure block
    assert "cli.py" not in tail.replace("hermes_cli", ""), (
        "entrypoint still execs cli.py — subcommands (setup/status) and "
        "non-interactive flags (chat -q/-Q) are unreachable"
    )
    assert "bin/hermes" in tail or "hermes_cli.main" in tail, (
        "entrypoint must dispatch through the hermes console entry "
        "(hermes_cli.main), the only surface with the full CLI"
    )


@pytest.mark.skipif(_console_script() is None, reason="no venv in this checkout")
def test_entrypoint_help_lists_the_full_cli_surface(tmp_path):
    env = dict(os.environ)
    env["JINN_AGENT_HOME"] = str(tmp_path)
    env.pop("HERMES_HOME", None)
    result = subprocess.run(
        [str(ENTRYPOINT), "--help"],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    for command in ("chat", "setup", "status"):
        assert command in result.stdout, (
            f"'{command}' missing from --help — the entrypoint is not "
            f"dispatching through the real CLI\n{result.stdout[-2000:]}"
        )


@pytest.mark.skipif(_console_script() is None, reason="no venv in this checkout")
def test_entrypoint_keeps_home_isolation(tmp_path):
    """The console-script dispatch must not lose the HERMES_HOME default."""
    env = dict(os.environ)
    env["JINN_AGENT_HOME"] = str(tmp_path)
    env.pop("HERMES_HOME", None)
    subprocess.run(
        [str(ENTRYPOINT), "--help"],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )
    # The config-ensure block runs against the resolved home on every
    # launch, so the isolated home must now hold the enablement.
    config = (tmp_path / "config.yaml").read_text(encoding="utf-8")
    assert "jinn" in config
