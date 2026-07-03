"""jinn-agent's default session chrome must show jinn-agent branding.

Regression for Jinn-Network/mono#1358: a cold clone's first screen showed
the upstream agent's branding (NOUS/Hermes banner art, 'Nous Research'
credit, Hermes/OpenClaw tips). The fork ships a `jinn` skin
(``plugins/jinn/skin/jinn.yaml``, installed to ``$HERMES_HOME/skins/`` by
``bin/jinn-agent``) and two surgically-owned upstream files
(``hermes_cli/banner.py`` version label + credit, ``hermes_cli/tips.py``
brand-filter tail).

The skin fixture mirrors tests/test_cli_skin_integration.py: skin state is
the process-global ``_active_skin``, so teardown MUST reset to ``default``
or it poisons the rest of the suite.
"""

from __future__ import annotations

import re
import shutil as _shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import yaml

# Import cli at module level (test_cli_skin_integration.py precedent):
# importing cli runs init_skin_from_config() as a module side effect, which
# would reset the active skin if the import happened inside a test body
# after the fixture activated the jinn skin.
from cli import _build_compact_banner  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
SKIN_FILE = REPO_ROOT / "plugins" / "jinn" / "skin" / "jinn.yaml"

# Upstream brand words that must never appear in jinn-agent session chrome.
# Lowercase `hermes` (command strings like `hermes update`) is functional,
# not branding, and is deliberately NOT matched.
BRAND_WORDS = re.compile(r"NOUS|Nous|\bHermes\b|OpenClaw")


@pytest.fixture()
def jinn_skin_active(tmp_path, monkeypatch):
    """Install the repo's jinn skin into an isolated home and activate it."""
    from hermes_cli.skin_engine import set_active_skin

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    skins = tmp_path / "skins"
    skins.mkdir()
    _shutil.copy(SKIN_FILE, skins / "jinn.yaml")
    set_active_skin("jinn")
    yield
    set_active_skin("default")


def test_jinn_skin_file_exists_and_is_complete():
    assert SKIN_FILE.is_file(), "plugins/jinn/skin/jinn.yaml is missing"
    raw = SKIN_FILE.read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    assert data["name"] == "jinn"

    branding = data.get("branding") or {}
    assert branding.get("agent_name") == "jinn-agent"
    for key in ("welcome", "goodbye", "response_label", "help_header",
                "prompt_symbol", "credit"):
        assert str(branding.get(key) or "").strip(), (
            f"branding.{key} must be set and non-empty "
            "(empty credit leaves a dangling '·' separator; the rest fall "
            "back to upstream Hermes strings)"
        )

    # banner_logo / banner_hero do NOT inherit through _build_skin_config —
    # an empty field falls back to the upstream Hermes art at render time
    # (skin_engine builds them from data.get(..., '') with no default merge).
    assert str(data.get("banner_logo") or "").strip(), "banner_logo required"
    assert str(data.get("banner_hero") or "").strip(), "banner_hero required"

    assert not BRAND_WORDS.search(raw), (
        f"upstream brand word in jinn.yaml: {BRAND_WORDS.search(raw).group(0)!r}"
    )


def test_version_label_uses_skin_agent_name(jinn_skin_active):
    from hermes_cli.banner import format_banner_version_label

    label = format_banner_version_label()
    assert label.startswith("jinn-agent v"), label


def test_compact_banner_clean(jinn_skin_active):
    from rich.console import Console

    with patch("cli.shutil.get_terminal_size",
               return_value=SimpleNamespace(columns=100)):
        banner = _build_compact_banner()

    console = Console(record=True, width=100)
    console.print(banner)
    text = console.export_text()

    assert "jinn-agent" in text
    assert not BRAND_WORDS.search(text), (
        f"upstream brand word in compact banner: "
        f"{BRAND_WORDS.search(text).group(0)!r}\n{text}"
    )


def test_full_welcome_banner_clean(jinn_skin_active):
    from rich.console import Console

    from hermes_cli.banner import build_welcome_banner
    from hermes_cli.skin_engine import get_active_skin

    console = Console(record=True, width=120)
    # Pin the terminal below the >=95-column logo threshold so the render is
    # deterministic; the logo/hero fields are asserted directly below.
    with patch("hermes_cli.banner.shutil.get_terminal_size",
               return_value=SimpleNamespace(columns=80)):
        build_welcome_banner(console, model="test-model", cwd=".", tools=[])
    text = console.export_text()

    assert "jinn-agent" in text
    assert not BRAND_WORDS.search(text), (
        f"upstream brand word in welcome banner: "
        f"{BRAND_WORDS.search(text).group(0)!r}\n{text}"
    )

    # The wide-terminal logo and the left-panel hero come straight from the
    # skin fields — assert the fields, not the width-gated render.
    skin = get_active_skin()
    assert not BRAND_WORDS.search(skin.banner_logo)
    assert not BRAND_WORDS.search(skin.banner_hero)


def test_welcome_line_from_skin(jinn_skin_active):
    from hermes_cli.skin_engine import get_active_skin

    welcome = get_active_skin().get_branding(
        "welcome",
        "Welcome to Hermes Agent! Type your message or /help for commands.",
    )
    assert "jinn-agent" in welcome
    assert not BRAND_WORDS.search(welcome)


def test_tips_have_no_upstream_branding():
    from hermes_cli.tips import TIPS

    claw = [t for t in TIPS if re.search(r"claw", t, re.IGNORECASE)]
    assert not claw, f"OpenClaw-era tips survive the fork filter: {claw}"

    hermes = [t for t in TIPS if re.search(r"\bHermes\b", t)]
    assert not hermes, f"capital-H Hermes branding in tips: {hermes[:3]}"

    assert any("jinn-agent" in t for t in TIPS), (
        "brand substitution produced no jinn-agent tips"
    )
