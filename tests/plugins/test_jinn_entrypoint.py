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
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINT = REPO_ROOT / "bin" / "jinn-agent"
SKIN_FILE = REPO_ROOT / "plugins" / "jinn" / "skin" / "jinn.yaml"


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


# ---------------------------------------------------------------------------
# Branding install (mono#1358) — the ensure-block must also install the jinn
# skin and default the config to it, so a cold clone's first screen says
# jinn-agent instead of the upstream branding. Pattern mirrors
# tests/plugins/test_jinn_plugin_loads.py (exec the single heredoc snippet).
# ---------------------------------------------------------------------------


def _ensure_snippet() -> str:
    script = ENTRYPOINT.read_text(encoding="utf-8")
    marker = "<<'PY" + "EOF'\n"
    end = "\nPY" + "EOF"
    assert marker in script, "entrypoint lost its config-ensure block"
    return script.split(marker)[1].split(end)[0]


def _run_ensure(home: Path) -> None:
    os.environ["HERMES_HOME"] = str(home)
    try:
        exec(compile(_ensure_snippet(), "ensure", "exec"), {"__name__": "__main__"})
    except SystemExit:
        pass


def test_entrypoint_installs_skin_and_branding_config(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("JINN_AGENT_REPO", str(REPO_ROOT))

    _run_ensure(tmp_path)

    installed = tmp_path / "skins" / "jinn.yaml"
    assert installed.is_file(), "jinn skin not installed into $HERMES_HOME/skins/"
    assert installed.read_text(encoding="utf-8") == SKIN_FILE.read_text(
        encoding="utf-8"
    ), "installed skin differs from the repo copy"

    cfg = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    assert cfg["display"]["skin"] == "jinn"
    # Upstream's OpenClaw-residue first-run hint is meaningless on this fork.
    assert cfg["onboarding"]["seen"]["openclaw_residue_cleanup"] is True

    # Idempotent: a second run changes nothing on disk.
    config_before = (tmp_path / "config.yaml").read_text(encoding="utf-8")
    skin_before = installed.read_text(encoding="utf-8")
    _run_ensure(tmp_path)
    assert (tmp_path / "config.yaml").read_text(encoding="utf-8") == config_before
    assert installed.read_text(encoding="utf-8") == skin_before


def test_entrypoint_respects_explicit_skin_choice(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("JINN_AGENT_REPO", str(REPO_ROOT))
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump({"display": {"skin": "mono"}}), encoding="utf-8"
    )

    _run_ensure(tmp_path)

    cfg = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    assert cfg["display"]["skin"] == "mono", "explicit skin choice was overwritten"
    # Skin still installed (available via /skin jinn) and the flag still set.
    assert (tmp_path / "skins" / "jinn.yaml").is_file()
    assert cfg["onboarding"]["seen"]["openclaw_residue_cleanup"] is True


def test_entrypoint_exports_repo_and_keeps_single_heredoc():
    script = ENTRYPOINT.read_text(encoding="utf-8")
    assert 'export JINN_AGENT_REPO="$PWD"' in script, (
        "entrypoint must export JINN_AGENT_REPO so the ensure-block can find "
        "the repo's skin file"
    )
    assert script.count("<<'PY" + "EOF'") == 1, (
        "entrypoint must keep exactly one heredoc block — the snippet tests "
        "split on the single PY" + "EOF marker"
    )
