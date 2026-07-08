"""Runtime output must hint `jinn-agent <subcmd>`, never `hermes <subcmd>`.

Regression for the de-hermes command-hint sweep. After the main
mono#1358/#1366 runtime-branding sweep, `hermes <cmd>` command-hint literals
survived across two legs of runtime output:

* the gateway leg — chat-delivered gateway messages (model-switch skew
  guard, /platform pause guidance, skill/bundle install hints, gateway
  already-running errors, kanban dispatcher guidance, update notifications);
* the CLI leg — ~65 literals in whatsapp next-steps and
  gateway/cron/backup/config guidance plus the Windows gateway prompts.

`hermes` resolves to a stock upstream install on the user's PATH, so every
one of those hints directed the user at the wrong binary.

Static coverage: scan the swept files' ASTs for `hermes <subcmd>` string
literals outside docstrings/help-kwargs/matcher allowlists (see
brandcheck.py for the conventions). The whole gateway package is swept so
files that are clean today stay clean; the CLI leg pins the six swept
hermes_cli files. Behavioural coverage for the model-switch skew-guard
message lives in tests/test_code_skew.py; the probes below exercise the few
CLI surfaces that can be built non-destructively. Never run gateway,
update, setup or uninstall here (uninstall self-deletes the repo checkout).
"""

from __future__ import annotations

import pytest

from tests.dehermes.brandcheck import (
    COMMAND_HINT,
    REPO_ROOT,
    scan_runtime_hint_violations,
)

SWEPT_FILES = sorted(
    p.relative_to(REPO_ROOT).as_posix()
    for p in (REPO_ROOT / "gateway").rglob("*.py")
) + [
    "hermes_cli/main.py",
    "hermes_cli/gateway.py",
    "hermes_cli/cron.py",
    "hermes_cli/backup.py",
    "hermes_cli/config.py",
    "hermes_cli/gateway_windows.py",
]


@pytest.mark.parametrize("rel_path", SWEPT_FILES)
def test_no_hermes_command_hints_in_runtime_strings(rel_path):
    violations = scan_runtime_hint_violations(rel_path)
    assert not violations, (
        f"{rel_path} has `hermes <subcmd>` command hints in runtime string "
        "literals (user's `hermes` is a different binary — hint must say "
        "`jinn-agent`):\n"
        + "\n".join(f"  line {ln}: {lit!r}" for ln, lit in violations)
    )


def test_fallback_update_command_is_jinn_agent():
    # The git-checkout fallback is the path jinn-agent installs actually
    # take (setup.sh clone + symlink); it must not recommend `hermes`.
    from hermes_cli.config import recommended_update_command_for_method

    cmd = recommended_update_command_for_method("git")
    assert cmd == "jinn-agent update"


def test_docker_update_message_has_no_hermes_hints():
    from hermes_cli.config import _DOCKER_UPDATE_MESSAGE

    hits = COMMAND_HINT.findall(_DOCKER_UPDATE_MESSAGE)
    assert not hits, f"hermes command hints in Docker update message: {hits}"


def test_user_systemd_error_hints_jinn_agent():
    # User-facing error message surface in gateway.py — safe to build
    # without touching any gateway state.
    from hermes_cli.gateway import (
        UserSystemdUnavailableError,
        _raise_user_systemd_unavailable,
    )

    with pytest.raises(UserSystemdUnavailableError) as exc_info:
        _raise_user_systemd_unavailable(
            "testuser", reason="test reason", fix_hint="    do the fix"
        )
    msg = str(exc_info.value)
    assert "jinn-agent gateway run" in msg
    assert not COMMAND_HINT.search(msg)
