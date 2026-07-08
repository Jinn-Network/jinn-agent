"""Regression tests for gateway.run._resolve_hermes_bin.

On this fork a bare ``hermes`` on PATH can belong to a stock upstream
install, not this checkout. The resolver feeds the detached /restart and
update flows, so resolving to the stock binary restarts the WRONG install.
It must prefer the fork's own entrypoint and never blindly use
``shutil.which("hermes")``.
"""

import importlib.util
import os
import shutil
import sys
from pathlib import Path

import gateway.run as run


REPO_ROOT = Path(run.__file__).resolve().parent.parent
FORK_ENTRYPOINT = REPO_ROOT / "bin" / "jinn-agent"


def _which_stock_hermes_only(cmd):
    """PATH lookup where only a stock upstream hermes is installed."""
    if cmd == "hermes":
        return "/usr/local/bin/hermes"
    return None


def test_prefers_fork_entrypoint_over_stock_hermes_on_path(monkeypatch):
    """The executable wrong-binary bug: stock hermes on PATH must lose."""
    assert FORK_ENTRYPOINT.is_file(), "fork entrypoint missing from checkout"
    monkeypatch.setattr(shutil, "which", _which_stock_hermes_only)

    resolved = run._resolve_hermes_bin()

    assert resolved == [str(FORK_ENTRYPOINT)]


def test_falls_back_to_path_installed_jinn_agent(monkeypatch, tmp_path):
    """No repo-local entrypoint: use the PATH-installed jinn-agent shim."""
    # Point the module's __file__ at a bare tmp dir so bin/jinn-agent is absent.
    monkeypatch.setattr(run, "__file__", str(tmp_path / "gateway" / "run.py"))
    monkeypatch.setattr(
        shutil,
        "which",
        lambda cmd: {
            "hermes": "/usr/local/bin/hermes",
            "jinn-agent": "/home/u/.local/bin/jinn-agent",
        }.get(cmd),
    )

    assert run._resolve_hermes_bin() == ["/home/u/.local/bin/jinn-agent"]


def test_falls_back_to_module_invocation_never_stock_hermes(monkeypatch, tmp_path):
    """No fork entrypoint anywhere: module fallback, NOT the stock hermes."""
    monkeypatch.setattr(run, "__file__", str(tmp_path / "gateway" / "run.py"))
    monkeypatch.setattr(shutil, "which", _which_stock_hermes_only)

    assert run._resolve_hermes_bin() == [sys.executable, "-m", "hermes_cli.main"]


def test_windows_skips_posix_shim_and_uses_module_invocation(monkeypatch):
    """bin/jinn-agent is a POSIX sh script — never returned on Windows."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(
        shutil,
        "which",
        lambda cmd: {
            "hermes": r"C:\hermes\hermes.exe",
            "jinn-agent": r"C:\somewhere\jinn-agent",
        }.get(cmd),
    )

    assert run._resolve_hermes_bin() == [sys.executable, "-m", "hermes_cli.main"]


def test_returns_none_when_nothing_resolvable(monkeypatch, tmp_path):
    monkeypatch.setattr(run, "__file__", str(tmp_path / "gateway" / "run.py"))
    monkeypatch.setattr(shutil, "which", _which_stock_hermes_only)
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)

    assert run._resolve_hermes_bin() is None
