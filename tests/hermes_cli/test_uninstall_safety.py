"""Safety tests for hermes_cli.uninstall.

Regression for the 2026-07-07 incident: ``get_project_root()`` resolves to the
checkout the CLI runs from, so ``uninstall --yes`` executed inside a git
working tree silently rmtree'd the whole repo (including uncommitted work and
a linked worktree), and ``remove_wrapper_script`` deleted ``~/.local/bin``
hermes launchers belonging to OTHER installs because it matched on file
content ('hermes_cli' / 'hermes-agent') rather than on where the launcher
actually points.

Three guarantees under test:
1. A project root that is a git working tree (``.git`` dir OR file — linked
   worktrees use a file) is never deleted unless ``--force-repo`` is passed.
2. ``--dry-run`` lists what would be removed and removes nothing.
3. Only launcher scripts/symlinks that resolve into THIS install's project
   root are removed; launchers for other installs survive.

All tests run against tmp_path fixtures — never the real installer/uninstaller
against this repo.
"""

import argparse
from pathlib import Path

import pytest

import hermes_cli.uninstall as uninstall
from hermes_cli.subcommands.uninstall import build_uninstall_parser


# ---------------------------------------------------------------------------
# Git working-tree guard
# ---------------------------------------------------------------------------

def _make_install(tmp_path: Path, name: str = "hermes-agent") -> Path:
    root = tmp_path / name
    (root / "venv" / "bin").mkdir(parents=True)
    (root / "venv" / "bin" / "hermes").write_text("#!/usr/bin/env python\nfrom hermes_cli.main import main\n")
    (root / "hermes_cli").mkdir()
    return root


def test_git_dir_is_working_tree(tmp_path):
    root = _make_install(tmp_path)
    (root / ".git").mkdir()
    assert uninstall._is_git_working_tree(root)


def test_git_file_is_working_tree(tmp_path):
    """Linked worktrees have a .git FILE, not a dir — must still be protected."""
    root = _make_install(tmp_path)
    (root / ".git").write_text("gitdir: /somewhere/else/.git/worktrees/wt\n")
    assert uninstall._is_git_working_tree(root)


def test_plain_dir_is_not_working_tree(tmp_path):
    root = _make_install(tmp_path)
    assert not uninstall._is_git_working_tree(root)


def test_refuses_to_delete_git_working_tree(tmp_path):
    root = _make_install(tmp_path)
    (root / ".git").mkdir()
    (root / "uncommitted.py").write_text("work in progress\n")

    deleted = uninstall.remove_project_root(root)

    assert deleted is False
    assert root.exists()
    assert (root / "uncommitted.py").exists()


def test_refuses_to_delete_linked_worktree(tmp_path):
    root = _make_install(tmp_path)
    (root / ".git").write_text("gitdir: /somewhere/else/.git/worktrees/wt\n")

    deleted = uninstall.remove_project_root(root)

    assert deleted is False
    assert root.exists()


def test_force_repo_deletes_git_working_tree(tmp_path):
    root = _make_install(tmp_path)
    (root / ".git").mkdir()

    deleted = uninstall.remove_project_root(root, force_repo=True)

    assert deleted is True
    assert not root.exists()


def test_deletes_non_git_install(tmp_path):
    root = _make_install(tmp_path)

    deleted = uninstall.remove_project_root(root)

    assert deleted is True
    assert not root.exists()


def test_dry_run_deletes_nothing(tmp_path):
    root = _make_install(tmp_path)

    deleted = uninstall.remove_project_root(root, dry_run=True)

    assert deleted is True  # it WOULD be removed
    assert root.exists()


def test_dry_run_with_force_repo_deletes_nothing(tmp_path):
    root = _make_install(tmp_path)
    (root / ".git").mkdir()

    deleted = uninstall.remove_project_root(root, force_repo=True, dry_run=True)

    assert deleted is True
    assert root.exists()


def test_missing_project_root_is_noop(tmp_path):
    assert uninstall.remove_project_root(tmp_path / "gone") is False


# ---------------------------------------------------------------------------
# Launcher wrapper scoping — only remove launchers belonging to THIS install
# ---------------------------------------------------------------------------

@pytest.fixture
def bin_dir(tmp_path, monkeypatch):
    """A fake ~/.local/bin, wired in as the only wrapper candidate location."""
    d = tmp_path / "local_bin"
    d.mkdir()
    monkeypatch.setattr(uninstall, "_wrapper_candidate_paths", lambda: [d / "hermes"])
    return d


def _write_shim(path: Path, install_root: Path):
    """The launcher install.sh writes: a bash shim exec'ing this install's venv."""
    path.write_text(
        "#!/usr/bin/env bash\n"
        "unset PYTHONPATH\n"
        "unset PYTHONHOME\n"
        f'exec "{install_root}/venv/bin/hermes" "$@"\n'
    )
    path.chmod(0o755)


def test_removes_shim_belonging_to_this_install(tmp_path, bin_dir):
    root = _make_install(tmp_path)
    _write_shim(bin_dir / "hermes", root)

    removed = uninstall.remove_wrapper_script(root)

    assert removed == [bin_dir / "hermes"]
    assert not (bin_dir / "hermes").exists()


def test_keeps_shim_belonging_to_other_install(tmp_path, bin_dir):
    """A launcher for a DIFFERENT hermes install mentions 'hermes-agent' in its
    path but must survive this install's uninstall."""
    root = _make_install(tmp_path, "hermes-agent")
    other = _make_install(tmp_path / "opt", "hermes-agent")
    _write_shim(bin_dir / "hermes", other)

    removed = uninstall.remove_wrapper_script(root)

    assert removed == []
    assert (bin_dir / "hermes").exists()


def test_removes_symlink_into_this_install(tmp_path, bin_dir):
    """Older installs symlinked ~/.local/bin/hermes at the venv entry point."""
    root = _make_install(tmp_path)
    link = bin_dir / "hermes"
    link.symlink_to(root / "venv" / "bin" / "hermes")

    removed = uninstall.remove_wrapper_script(root)

    assert removed == [link]
    assert not link.is_symlink()


def test_keeps_symlink_to_other_install(tmp_path, bin_dir):
    """The incident case: a stock-hermes symlink whose TARGET contains
    'hermes_cli' imports must not be deleted by another install's uninstall."""
    root = _make_install(tmp_path, "hermes-agent")
    other = _make_install(tmp_path / "stock", "hermes-agent")
    link = bin_dir / "hermes"
    link.symlink_to(other / "venv" / "bin" / "hermes")

    removed = uninstall.remove_wrapper_script(root)

    assert removed == []
    assert link.is_symlink()
    assert link.resolve() == (other / "venv" / "bin" / "hermes").resolve()


def test_removes_dangling_symlink_into_this_install(tmp_path, bin_dir):
    root = _make_install(tmp_path)
    link = bin_dir / "hermes"
    link.symlink_to(root / "venv" / "bin" / "hermes")
    (root / "venv" / "bin" / "hermes").unlink()  # dangle it

    removed = uninstall.remove_wrapper_script(root)

    assert removed == [link]
    assert not link.is_symlink()


def test_wrapper_dry_run_reports_but_keeps(tmp_path, bin_dir):
    root = _make_install(tmp_path)
    _write_shim(bin_dir / "hermes", root)

    removed = uninstall.remove_wrapper_script(root, dry_run=True)

    assert removed == [bin_dir / "hermes"]
    assert (bin_dir / "hermes").exists()


# ---------------------------------------------------------------------------
# Node symlink dry-run
# ---------------------------------------------------------------------------

def test_node_symlinks_dry_run_reports_but_keeps(tmp_path, monkeypatch):
    hermes_home = tmp_path / ".hermes"
    node_bin = hermes_home / "node" / "bin"
    node_bin.mkdir(parents=True)
    for name in ("node", "npm", "npx"):
        (node_bin / name).write_text("#!/bin/sh\n")

    local_bin = tmp_path / "local_bin"
    local_bin.mkdir()
    for name in ("node", "npm", "npx"):
        (local_bin / name).symlink_to(node_bin / name)
    monkeypatch.setattr(uninstall, "_node_symlink_candidate_dirs", lambda: [local_bin])

    removed = uninstall.remove_node_symlinks(hermes_home, dry_run=True)

    assert sorted(p.name for p in removed) == ["node", "npm", "npx"]
    for name in ("node", "npm", "npx"):
        assert (local_bin / name).is_symlink()


# ---------------------------------------------------------------------------
# Full uninstall must not bypass the git guard via rmtree(hermes_home)
# ---------------------------------------------------------------------------

def test_full_uninstall_keeps_hermes_home_when_git_checkout_inside(
    tmp_path, monkeypatch
):
    """Standard layout: the checkout lives at $HERMES_HOME/hermes-agent. When
    the git guard refuses to delete it, the full-uninstall rmtree(hermes_home)
    must not sweep it away anyway."""
    hermes_home = tmp_path / ".hermes"
    root = _make_install(hermes_home, "hermes-agent")
    (root / ".git").mkdir()
    (hermes_home / "config.yaml").write_text("x: 1\n")

    # Neutralise every step that touches the real system.
    monkeypatch.setattr(uninstall, "uninstall_gateway_service", lambda: False)
    monkeypatch.setattr(uninstall, "remove_path_from_shell_configs", lambda: [])
    monkeypatch.setattr(uninstall, "_wrapper_candidate_paths", lambda: [])
    monkeypatch.setattr(uninstall, "_node_symlink_candidate_dirs", lambda: [])
    import hermes_cli.gui_uninstall as gui_uninstall
    monkeypatch.setattr(gui_uninstall, "uninstall_gui", lambda home: [])

    uninstall._perform_uninstall(
        project_root=root,
        hermes_home=hermes_home,
        full_uninstall=True,
        remove_profiles=False,
        named_profiles=[],
        force_repo=False,
    )

    assert root.exists()
    assert (root / ".git").exists()


# ---------------------------------------------------------------------------
# CLI flags
# ---------------------------------------------------------------------------

def _parse(argv):
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    build_uninstall_parser(subparsers, cmd_uninstall=lambda args: None)
    return parser.parse_args(argv)


def test_parser_accepts_dry_run_and_force_repo():
    args = _parse(["uninstall", "--yes", "--dry-run", "--force-repo"])
    assert args.dry_run is True
    assert args.force_repo is True


def test_parser_defaults_are_safe():
    args = _parse(["uninstall", "--yes"])
    assert args.dry_run is False
    assert args.force_repo is False


# ---------------------------------------------------------------------------
# run_uninstall --dry-run end-to-end: prints the plan, touches nothing
# ---------------------------------------------------------------------------

def test_run_uninstall_dry_run_touches_nothing(tmp_path, monkeypatch, capsys):
    root = _make_install(tmp_path)
    (root / ".git").mkdir()
    hermes_home = tmp_path / ".hermes"
    node_bin = hermes_home / "node" / "bin"
    node_bin.mkdir(parents=True)
    (node_bin / "node").write_text("#!/bin/sh\n")

    local_bin = tmp_path / "local_bin"
    local_bin.mkdir()
    _write_shim(local_bin / "hermes", root)
    (local_bin / "node").symlink_to(node_bin / "node")

    monkeypatch.setattr(uninstall, "get_project_root", lambda: root)
    monkeypatch.setattr(uninstall, "get_hermes_home", lambda: hermes_home)
    monkeypatch.setattr(uninstall, "_wrapper_candidate_paths", lambda: [local_bin / "hermes"])
    monkeypatch.setattr(uninstall, "_node_symlink_candidate_dirs", lambda: [local_bin])

    args = argparse.Namespace(yes=True, full=True, dry_run=True, force_repo=False)
    uninstall.run_uninstall(args)

    # Nothing was removed.
    assert root.exists()
    assert (local_bin / "hermes").exists()
    assert (local_bin / "node").is_symlink()
    assert hermes_home.exists()

    out = capsys.readouterr().out
    assert "dry run" in out.lower() or "dry-run" in out.lower()
    assert str(root) in out
    # The git guard is surfaced in the plan.
    assert "git" in out.lower()
