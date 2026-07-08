"""Runtime output must hint `jinn-agent <subcmd>`, never `hermes <subcmd>`.

Regression for the gateway leg of the de-hermes command-hint sweep: after
the main mono#1358/#1366 runtime-branding sweep, `hermes <cmd>` command-hint
literals survived in chat-delivered gateway messages (model-switch skew
guard, /platform pause guidance, skill/bundle install hints, gateway
already-running errors, kanban dispatcher guidance, update notifications).
`hermes` resolves to a stock upstream install on the user's PATH, so every
one of those hints directed the user at the wrong binary.

Static coverage: scan the swept files' ASTs for `hermes <subcmd>` string
literals outside docstrings/help-kwargs/matcher allowlists (see
brandcheck.py for the conventions). The whole gateway package is swept so
files that are clean today stay clean. Behavioural coverage for the
model-switch skew-guard message lives in tests/test_code_skew.py. Never
run gateway, update, setup or uninstall here (uninstall self-deletes the
repo checkout).
"""

from __future__ import annotations

import pytest

from tests.dehermes.brandcheck import (
    REPO_ROOT,
    scan_runtime_hint_violations,
)

SWEPT_FILES = sorted(
    p.relative_to(REPO_ROOT).as_posix()
    for p in (REPO_ROOT / "gateway").rglob("*.py")
)


@pytest.mark.parametrize("rel_path", SWEPT_FILES)
def test_no_hermes_command_hints_in_runtime_strings(rel_path):
    violations = scan_runtime_hint_violations(rel_path)
    assert not violations, (
        f"{rel_path} has `hermes <subcmd>` command hints in runtime string "
        "literals (user's `hermes` is a different binary — hint must say "
        "`jinn-agent`):\n"
        + "\n".join(f"  line {ln}: {lit!r}" for ln, lit in violations)
    )
