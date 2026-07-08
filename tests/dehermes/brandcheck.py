"""De-hermes brand-check conventions for the jinn-agent fork.

The human-facing binary is ``jinn-agent`` (bin/jinn-agent). A plain
``hermes`` on the user's PATH resolves to a *different*, stock upstream
install, so any RUNTIME output that tells the user to run
``hermes <subcommand>`` is a wrong-binary correctness defect
(same class as the mono#1358/#1366 runtime-branding sweep).

Conventions enforced here (mirrors the tips.py filter rules):

- Command hints in runtime output are hard-replaced to ``jinn-agent
  <subcommand>`` in the source literal — never routed through a skin/brand
  accessor. Hints must be greppable and true at rest.
- Technical tokens stay untouched: HERMES_* env vars, ``hermes_cli``
  module paths, ``~/.hermes`` state paths, service/unit names
  (``hermes-gateway.service``), package names (``hermes-agent``), docker
  image names, tmux session names (``tmux new -s hermes``), and
  process/unit cmdline matchers.
- ``--help`` surfaces (argparse ``help=`` / ``description=`` /
  ``epilog=`` / ``usage=``) and developer-facing comments/docstrings are
  out of scope for this checker.

``scan_runtime_hint_violations`` statically scans a source file's AST for
string constants that contain a ``hermes <subcommand>`` command hint,
excluding docstrings, argparse help-ish kwargs, and an explicit per-file
allowlist of exact technical literals.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# A command hint: the word `hermes` followed by a subcommand word.
# The lookbehind rejects module paths (hermes_cli), state paths
# (~/.hermes/), unit names (hermes-gateway) and "/hermes gateway"-style
# cmdline matchers; flag-only forms (`hermes -c`) don't match either.
COMMAND_HINT = re.compile(r"(?<![\w/.-])hermes ([a-z][a-z0-9-]*)")

# argparse surfaces excluded from the runtime sweep (help text is a
# separate, deliberate exclusion — see the sweep spec).
HELP_KWARGS = frozenset({"help", "description", "epilog", "usage"})

# Exact string literals that legitimately contain `hermes <word>`:
# process-cmdline / unit-file matchers and log-file grep markers. Keyed by
# repo-relative path; matched against the *whole* literal, so a matcher
# entry can never mask a regression elsewhere in the file.
ALLOWED_LITERALS: dict[str, frozenset[str]] = {
    # _dashboard_pids() cmdline matchers: they match how the process was
    # actually spawned (python entry point), not what the user typed.
    "hermes_cli/main.py": frozenset({
        "hermes dashboard",
        "hermes serve",
        # update-log banner marker (grep target in the log file, not a hint)
        "\n=== hermes update started ",
    }),
    # _LEGACY_UNIT_EXECSTART_MARKERS: identify pre-rename systemd units by
    # their ExecStart content.
    "hermes_cli/gateway.py": frozenset({
        " hermes gateway ",
        "/hermes gateway ",
    }),
    # logger.error diagnostic naming what _resolve_hermes_bin() actually
    # probed for (`which("hermes")` / hermes_cli module) — a binary-resolution
    # fact, not a command hint the user should type.
    "gateway/run.py": frozenset({
        "Could not locate hermes binary for detached /restart",
    }),
}


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """Return id()s of Constant nodes that are docstrings."""
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(
                    body[0].value, ast.Constant) and isinstance(
                    body[0].value.value, str):
                ids.add(id(body[0].value))
    return ids


def _help_kwarg_nodes(tree: ast.AST) -> set[int]:
    """Return id()s of Constant nodes under argparse help-ish kwargs."""
    ids: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if kw.arg in HELP_KWARGS:
                for sub in ast.walk(kw.value):
                    if isinstance(sub, ast.Constant) and isinstance(
                            sub.value, str):
                        ids.add(id(sub))
    return ids


def scan_runtime_hint_violations(rel_path: str) -> list[tuple[int, str]]:
    """Scan *rel_path* (repo-relative) for `hermes <subcmd>` hints in
    runtime string literals.

    Returns ``[(lineno, literal), ...]`` for every offending literal.
    Comments never appear (not in the AST); docstrings, help-ish kwargs
    and the per-file ALLOWED_LITERALS are excluded.
    """
    source = (REPO_ROOT / rel_path).read_text(encoding="utf-8")
    tree = ast.parse(source, filename=rel_path)
    skip = _docstring_nodes(tree) | _help_kwarg_nodes(tree)
    allowed = ALLOWED_LITERALS.get(rel_path, frozenset())

    violations: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        if id(node) in skip or node.value in allowed:
            continue
        if COMMAND_HINT.search(node.value):
            violations.append((node.lineno, node.value))
    return violations
