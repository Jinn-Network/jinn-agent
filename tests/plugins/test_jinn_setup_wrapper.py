"""``setup.sh`` must be fork-aware, not a bare pass-through to the installer.

Regression for the second cold-clone dogfood run (2026-07-03). Run as a
plain wrapper, the upstream installer:

  1. synced bundled skills into ``~/.hermes/skills/`` while the runtime
     (``bin/jinn-agent``) reads ``~/.jinn-agent`` — bundled skills were
     invisible to every session;
  2. ran ``ln -sf <repo>/venv/bin/hermes ~/.local/bin/hermes``, silently
     repointing a stock upstream install's ``hermes`` command at the fork
     (observed live on an operator machine);
  3. closed with upstream-branded next steps (``hermes setup`` / ``hermes``)
     that bypass the fork entrypoint.

Mono issue: Jinn-Network/mono#1360.

These tests run the REAL ``setup.sh`` against a stub ``setup-hermes.sh``
that mimics the two side effects that matter (records ``$HERMES_HOME``,
clobbers the ``hermes`` link) — the full installer is far too heavy for CI.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

STUB_INSTALLER = """#!/bin/sh
# Stub of the upstream installer: the two side effects under test.
echo "$HERMES_HOME" > "$(dirname "$0")/recorded-home"
mkdir -p "$HOME/.local/bin"
ln -sf "$(cd "$(dirname "$0")" && pwd)/venv/bin/hermes" "$HOME/.local/bin/hermes"
"""


@pytest.fixture()
def sandbox(tmp_path):
    """A fake $HOME plus a repo copy whose installer is the stub."""
    home = tmp_path / "home"
    home.mkdir()
    repo = tmp_path / "repo"
    (repo / "bin").mkdir(parents=True)
    shutil.copy2(REPO_ROOT / "setup.sh", repo / "setup.sh")
    (repo / "bin" / "jinn-agent").write_text("#!/bin/sh\n", encoding="utf-8")
    stub = repo / "setup-hermes.sh"
    stub.write_text(STUB_INSTALLER, encoding="utf-8")
    for path in (repo / "setup.sh", stub, repo / "bin" / "jinn-agent"):
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return home, repo


def _run(home: Path, repo: Path, **extra_env: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in ("HERMES_HOME", "JINN_AGENT_HOME")}
    env["HOME"] = str(home)
    env.update(extra_env)
    return subprocess.run(
        ["/bin/sh", str(repo / "setup.sh")],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
        cwd=str(repo),
    )


def test_installer_runs_against_the_jinn_agent_home(sandbox):
    home, repo = sandbox
    result = _run(home, repo)
    assert result.returncode == 0, result.stderr
    recorded = (repo / "recorded-home").read_text().strip()
    assert recorded == str(home / ".jinn-agent")


def test_jinn_agent_home_override_is_respected(sandbox):
    home, repo = sandbox
    result = _run(home, repo, JINN_AGENT_HOME=str(home / "custom"))
    assert result.returncode == 0, result.stderr
    assert (repo / "recorded-home").read_text().strip() == str(home / "custom")


def test_preexisting_hermes_link_is_preserved(sandbox):
    home, repo = sandbox
    link_dir = home / ".local" / "bin"
    link_dir.mkdir(parents=True)
    stock_target = home / "stock-hermes-install" / "venv" / "bin" / "hermes"
    (link_dir / "hermes").symlink_to(stock_target)
    result = _run(home, repo)
    assert result.returncode == 0, result.stderr
    assert (link_dir / "hermes").is_symlink()
    assert os.readlink(link_dir / "hermes") == str(stock_target), (
        "a stock install's hermes command was repointed at the fork"
    )


def test_no_hermes_link_is_left_behind_when_none_existed(sandbox):
    home, repo = sandbox
    result = _run(home, repo)
    assert result.returncode == 0, result.stderr
    hermes_link = home / ".local" / "bin" / "hermes"
    assert not hermes_link.exists() and not hermes_link.is_symlink(), (
        "setup left an upstream-named command on PATH"
    )


def test_jinn_agent_command_link_is_created(sandbox):
    home, repo = sandbox
    result = _run(home, repo)
    assert result.returncode == 0, result.stderr
    link = home / ".local" / "bin" / "jinn-agent"
    assert link.is_symlink()
    assert os.readlink(link) == str(repo / "bin" / "jinn-agent")


def test_hermes_link_is_restored_even_when_the_installer_fails(sandbox):
    home, repo = sandbox
    (repo / "setup-hermes.sh").write_text(
        STUB_INSTALLER + "exit 7\n", encoding="utf-8"
    )
    link_dir = home / ".local" / "bin"
    link_dir.mkdir(parents=True)
    stock_target = home / "stock" / "hermes"
    (link_dir / "hermes").symlink_to(stock_target)
    result = _run(home, repo)
    assert result.returncode != 0
    assert os.readlink(link_dir / "hermes") == str(stock_target)


def test_next_steps_name_only_fork_commands(sandbox):
    home, repo = sandbox
    result = _run(home, repo)
    assert result.returncode == 0, result.stderr
    assert "jinn-agent" in result.stdout
    # The wrapper's own closing block must not tell the user to run the
    # upstream command. (The stub prints nothing, so any 'hermes setup' /
    # bare 'hermes' instruction here would come from the wrapper.)
    assert "hermes setup" not in result.stdout
