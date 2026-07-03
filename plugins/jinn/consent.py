"""Consent state + first-run flow for the Jinn layer.

Consent is a three-value machine: ``unset -> accepted | declined``.
``unset`` and ``declined`` behave identically at capture time — nothing
leaves the machine. The safe default (bare Enter) is decline.

Copy is verbatim from the design artifact
(mono: docs/design/artifacts/2026-07-02-1312-fork-consent-ledger/):
plain language wherever data leaves the machine, no emoji, no metaphor.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, Optional

logger = logging.getLogger(__name__)

try:
    from hermes_constants import get_hermes_home
except Exception:  # pragma: no cover — plugin may load before constants resolves

    def get_hermes_home() -> Path:  # type: ignore[no-redef]
        val = (os.environ.get("HERMES_HOME") or "").strip()
        return Path(val).resolve() if val else (Path.home() / ".hermes").resolve()


UNSET = "unset"
ACCEPTED = "accepted"
DECLINED = "declined"

# ── Exact copy (design artifact) ─────────────────────────────────────────────

OPENING = (
    "jinn-agent is an open coding harness. When it finishes a task it can "
    "publish a scrubbed trace of that task to a public corpus — the shared "
    "record that trains the harness everyone runs."
)

WHY = [
    "Build the open harness — your tasks improve the agent no one company owns.",
    "Earn rewards — verified contributions earn OLAS.",
    "Two-way — you read from the same corpus you feed.",
]

WHAT_LEAVES = [
    "Only traces of tasks this harness runs — never your machine, shell, files, "
    "or anything outside a task.",
    "Every trace is scrubbed of secrets and personal data here, first. If "
    "scrubbing can't finish, nothing sends. It fails closed.",
    "You can veto any task, and preview the exact payload before the first send.",
]

DECLINE_LINE = "Decline and jinn-agent still works fully — as a reader."

CONFIRM_ACCEPT = (
    "Turn on contribution? Every task this harness runs will be scrubbed and "
    "published to the public corpus. You can veto any task and turn this off "
    "any time. [Y] Yes · [N] No"
)
CONFIRM_DECLINE = (
    "Decline contribution? The harness stays fully functional — it will read "
    "the corpus and publish nothing. [Y] Yes · [N] No"
)
RECORDED_ON = (
    "Contribution is ON. Scrubbed task traces will publish to the public "
    "corpus. Nothing publishes until after you preview once."
)
RECORDED_OFF = (
    "Contribution is OFF — reader only. No trace leaves this machine. "
    "Turn on any time: /jinn consent"
)
NODE_STUB = (
    "Run a network node? Running a node executes tasks for others and earns "
    "rewards. Separate setup; not needed to contribute or read. "
    "[L] Later — show docs · [Enter] Skip"
)
NODE_STUB_LATER = (
    "See docs.jinn.network/run-a-node when you're ready. Nothing to do now."
)

KEYS_LINE = "[A] Accept · [D] Decline · [P] Preview a scrubbed envelope · [?] Docs"

# The slash-command surface (TUI-safe: no blocking reads — see run_consent_flow's
# docstring). Same deliberate two-step as the keyboard flow.
COMMANDS_LINE = (
    "Accept: /jinn consent accept · Decline: /jinn consent decline · "
    "Preview a scrubbed envelope first: /jinn preview · Docs: docs.jinn.network/harness"
)


# ── State store ──────────────────────────────────────────────────────────────

def state_path() -> Path:
    return get_hermes_home() / "jinn" / "consent.json"


def load_state() -> Dict[str, object]:
    path = state_path()
    if not path.exists():
        return {"status": UNSET, "previewed": False}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("status") not in (UNSET, ACCEPTED, DECLINED):
            return {"status": UNSET, "previewed": False}
        data.setdefault("previewed", False)
        return data
    except Exception:
        logger.warning("jinn: unreadable consent state at %s — treating as unset", path)
        return {"status": UNSET, "previewed": False}


def save_state(status: str, *, previewed: Optional[bool] = None) -> Dict[str, object]:
    if status not in (UNSET, ACCEPTED, DECLINED):
        raise ValueError(f"invalid consent status: {status}")
    current = load_state()
    state: Dict[str, object] = {
        "status": status,
        "previewed": bool(current.get("previewed") if previewed is None else previewed),
        "recordedAt": datetime.now(timezone.utc).isoformat(),
    }
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    return state


def mark_previewed() -> None:
    current = load_state()
    save_state(str(current.get("status", UNSET)), previewed=True)


def capture_enabled() -> bool:
    """True only when the operator explicitly accepted. unset == declined."""
    return load_state().get("status") == ACCEPTED


# ── The flow ─────────────────────────────────────────────────────────────────

def render_explainer(keys_line: str = KEYS_LINE) -> str:
    lines = [OPENING, ""]
    lines += [f"  {s}" for s in WHY]
    lines.append("")
    lines.append("What leaves this machine:")
    lines += [f"  {s}" for s in WHAT_LEAVES]
    lines.append("")
    lines.append(DECLINE_LINE)
    lines.append("")
    lines.append(keys_line)
    return "\n".join(lines)


def confirm_accept_command() -> str:
    return CONFIRM_ACCEPT.replace("[Y] Yes · [N] No", "To confirm: /jinn consent accept confirm")


def confirm_decline_command() -> str:
    return CONFIRM_DECLINE.replace("[Y] Yes · [N] No", "To confirm: /jinn consent decline confirm")


def record_accept() -> str:
    save_state(ACCEPTED)
    return RECORDED_ON + "\n\n" + NODE_STUB_LATER


def record_decline() -> str:
    save_state(DECLINED)
    return RECORDED_OFF


def run_consent_flow(
    input_fn: Callable[[str], str],
    print_fn: Callable[[str], None],
    preview_fn: Optional[Callable[[], None]] = None,
) -> str:
    """The first-run consent flow for a PLAIN TERMINAL (blocking reads).

    Do NOT call from a TUI slash-command handler — ``input()`` blocks on
    stdin the TUI owns and deadlocks the session (first cold-clone dogfood
    finding, 2026-07-03). The slash surface uses the stateless
    ``/jinn consent accept|decline [confirm]`` commands instead.

    Returns the recorded status.

    ``unset -> accepted | declined``; per-action lifecycle
    ``idle -> confirming -> recorded``. Bare Enter defaults to decline —
    the safe default never publishes.
    """
    print_fn(render_explainer())
    while True:
        choice = input_fn("> ").strip().lower()
        if choice == "p" and preview_fn is not None:
            preview_fn()
            print_fn("[A] Accept · [B] Back")
            continue
        if choice == "?":
            print_fn("Docs: docs.jinn.network/harness — consent, scrubbing, and the corpus.")
            continue
        if choice == "a":
            confirm = input_fn(CONFIRM_ACCEPT + "\n> ").strip().lower()
            if confirm == "y":
                save_state(ACCEPTED)
                print_fn(RECORDED_ON)
                break
            continue
        # Bare Enter, 'd', or anything unrecognised routes to decline —
        # but decline still takes one deliberate confirmation.
        confirm = input_fn(CONFIRM_DECLINE + "\n> ").strip().lower()
        if confirm == "y":
            save_state(DECLINED)
            print_fn(RECORDED_OFF)
            break

    status = str(load_state().get("status"))
    node = input_fn(NODE_STUB + "\n> ").strip().lower()
    if node == "l":
        print_fn(NODE_STUB_LATER)
    return status
