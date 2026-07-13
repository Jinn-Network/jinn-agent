"""/jinn skills — install corpus-published skills into Hermes's native skills.

Closes the seed-consumption loop (mono #1345): a skill published to the
corpus becomes a locally installed Hermes skill — prefer
``jinn-layer skills install <ref>`` (handles both first-class ``jinn.skill.v1``
and the seeded trace fallback), else ``jinn-layer corpus get <ref>`` → sha256
verification → ``extract_skill`` → write
``$HERMES_HOME/skills/<slug>/SKILL.md`` — and Hermes's native loader takes
over from there.

Consuming is ALWAYS allowed: no consent state is consulted anywhere in this
module. Consent gates contributing (capture/publish), never reading.

Every install writes a ``.jinn-ref`` marker (the corpus ref) next to the
SKILL.md; ``uninstall`` refuses to touch a skill directory without the
marker, so a user's own skills can never be deleted through this surface.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import jinn_layer
from .consent import get_hermes_home

logger = logging.getLogger(__name__)

SKILL_ARTIFACT_TYPE = "jinn.skill.v1"
TRACE_ENVELOPE_ARTIFACT_TYPE = "jinn.trace-envelope.v0"
MARKER_FILE = ".jinn-ref"

_SLUG_RE = re.compile(r"[^a-zA-Z0-9._-]+")


@dataclass
class ExtractedSkill:
    skill_md: str
    slug: str
    sha256: str
    shape: str  # 'jinn.skill.v1' | 'seeded-trace'
    tier: str
    summary: str
    companion_files: List[Tuple[str, bytes]] = field(default_factory=list)


def skills_dir() -> Path:
    return get_hermes_home() / "skills"


def _sanitise_slug(raw: str) -> str:
    """Directory-safe slug: no separators, no traversal, non-empty."""
    slug = _SLUG_RE.sub("-", raw.strip()).strip(".-")
    if not slug:
        raise ValueError(f"cannot derive a usable skill slug from {raw!r}")
    return slug[:80]


def _verify_artifact_bytes(artifact: Dict[str, Any]) -> Tuple[bytes, str]:
    content_b64 = artifact.get("contentBase64")
    expected = str(artifact.get("sha256") or "")
    if not isinstance(content_b64, str) or not expected:
        raise ValueError("artifact is missing content or sha256")
    content = base64.b64decode(content_b64)
    actual = hashlib.sha256(content).hexdigest()
    if actual != expected:
        raise ValueError(
            f"sha256 mismatch — refusing to install (expected {expected[:12]}…, got {actual[:12]}…)"
        )
    return content, expected


def _frontmatter_name(skill_md: str) -> Optional[str]:
    match = re.match(r"^---\r?\n([\s\S]*?)\r?\n---", skill_md)
    if not match:
        return None
    for line in match.group(1).splitlines():
        if line.startswith("name:"):
            name = line.split(":", 1)[1].strip().strip("'\"")
            return name or None
    return None


def _skill_from_v1_obj(skill: Dict[str, Any], sha256: str, ref: str) -> ExtractedSkill:
    body = skill.get("skill")
    if not isinstance(body, dict):
        raise ValueError("jinn.skill.v1 artifact is missing skill body")
    skill_md = body.get("skillMd")
    if not isinstance(skill_md, str) or not skill_md.strip():
        raise ValueError("jinn.skill.v1 artifact has no skillMd")
    provenance = skill.get("provenance") if isinstance(skill.get("provenance"), dict) else {}
    tier = str(provenance.get("verifiabilityTier") or "")
    name = str(body.get("name") or _frontmatter_name(skill_md) or ref)
    summary = str(body.get("description") or name or ref)[:120]
    companion_files: List[Tuple[str, bytes]] = []
    for entry in skill.get("files") or []:
        if not isinstance(entry, dict):
            continue
        path = entry.get("path")
        content_b64 = entry.get("contentBase64")
        expected_hash = str(entry.get("sha256") or "")
        if not isinstance(path, str) or not isinstance(content_b64, str):
            continue
        content = base64.b64decode(content_b64)
        if expected_hash:
            actual = hashlib.sha256(content).hexdigest()
            if actual != expected_hash:
                raise ValueError(f"companion file {path} sha256 mismatch")
        companion_files.append((path, content))
    return ExtractedSkill(
        skill_md=skill_md,
        slug=_sanitise_slug(name),
        sha256=sha256,
        shape=SKILL_ARTIFACT_TYPE,
        tier=tier,
        summary=summary,
        companion_files=companion_files,
    )


def _skill_from_trace(trace: Dict[str, Any], sha256: str, ref: str) -> Optional[ExtractedSkill]:
    skill_md, slug = _skill_md_and_slug(trace, ref)
    tier = str(((trace.get("outcome") or {}).get("verifiabilityTier")) or "")
    summary = str(((trace.get("task") or {}).get("summary")) or ref)[:120]
    return ExtractedSkill(
        skill_md=skill_md,
        slug=slug,
        sha256=sha256,
        shape="seeded-trace",
        tier=tier,
        summary=summary,
    )


def extract_skill(record: Dict[str, Any], ref: str) -> Optional[ExtractedSkill]:
    """Return the installable skill from a corpus-get record, or None.

    Prefers a first-class ``jinn.skill.v1`` artifact (distilled skill-only
    records) and falls back to the seeded trace shape (trace envelope →
    ``seed:skill-md`` step → ``skill.md`` attribute).
    """
    artifacts = record.get("artifacts")
    if not isinstance(artifacts, list):
        return None

    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        if artifact.get("artifactType") != SKILL_ARTIFACT_TYPE:
            continue
        content, sha256 = _verify_artifact_bytes(artifact)
        parsed = json.loads(content.decode("utf-8"))
        if not isinstance(parsed, dict):
            raise ValueError("jinn.skill.v1 artifact did not parse to an object")
        return _skill_from_v1_obj(parsed, sha256, ref)

    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        if artifact.get("artifactType") != TRACE_ENVELOPE_ARTIFACT_TYPE:
            continue
        content, sha256 = _verify_artifact_bytes(artifact)
        trace = json.loads(content.decode("utf-8"))
        if not isinstance(trace, dict):
            return None
        return _skill_from_trace(trace, sha256, ref)

    return None


def _extract_trace(record: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
    """Return (trace envelope, verified sha256) from a corpus-get record.

    Verifies the artifact bytes against the record's sha256 BEFORE parsing —
    a mismatch refuses the install outright.
    """
    artifacts = record.get("artifacts")
    if not isinstance(artifacts, list):
        raise ValueError("corpus record has no artifacts")
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        if artifact.get("artifactType") != TRACE_ENVELOPE_ARTIFACT_TYPE:
            continue
        content, sha256 = _verify_artifact_bytes(artifact)
        return json.loads(content.decode("utf-8")), sha256
    raise ValueError(f"no {TRACE_ENVELOPE_ARTIFACT_TYPE} artifact in this record")


def _skill_md_and_slug(trace: Dict[str, Any], ref: str) -> Tuple[str, str]:
    steps = trace.get("steps")
    if not isinstance(steps, list):
        raise ValueError("trace envelope has no steps")
    for step in steps:
        if not isinstance(step, dict):
            continue
        attrs = step.get("attributes")
        if not isinstance(attrs, dict):
            continue
        skill_md = attrs.get("skill.md")
        if not isinstance(skill_md, str) or not skill_md.strip():
            continue
        attribution = attrs.get("seed.attribution")
        raw_slug: Optional[str] = None
        if isinstance(attribution, dict) and isinstance(attribution.get("skill"), str):
            raw_slug = str(attribution["skill"]).split("/")[-1]
        if raw_slug is None:
            tags = (trace.get("task") or {}).get("distributionTags")
            if isinstance(tags, list):
                candidates = [t for t in tags if isinstance(t, str) and t not in ("seed-import",)]
                raw_slug = candidates[-1] if candidates else None
        return skill_md, _sanitise_slug(raw_slug or ref)
    raise ValueError("no skill.md content in this envelope — not an installable skill record")


def _write_skill_tree(target: Path, extracted: ExtractedSkill, ref: str) -> str:
    target.mkdir(parents=True, exist_ok=True)
    (target / "SKILL.md").write_text(extracted.skill_md, encoding="utf-8")
    for relpath, content in extracted.companion_files:
        dest = target / relpath
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
    (target / MARKER_FILE).write_text(
        json.dumps({"ref": ref, "sha256": extracted.sha256}) + "\n",
        encoding="utf-8",
    )
    return str(target / "SKILL.md")


def _install_via_cli(ref: str, runner: jinn_layer.Runner) -> Optional[str]:
    staging = Path(tempfile.mkdtemp(prefix="jinn-skill-install-"))
    moved = False
    try:
        code, out = jinn_layer.run(
            ["skills", "install", ref, "--json", "--out", str(staging)],
            runner,
        )
        if code != 0:
            return None
        result = json.loads(out)
        slug = _sanitise_slug(str(result.get("name") or staging.name))
        target = skills_dir() / slug
        if target.exists():
            shutil.rmtree(target)
        shutil.move(str(staging), str(target))
        moved = True
        code2, out2 = jinn_layer.run(["corpus", "get", ref, "--json"], runner)
        sha256 = ""
        if code2 == 0:
            extracted = extract_skill(json.loads(out2), ref)
            if extracted is not None:
                sha256 = extracted.sha256
        (target / MARKER_FILE).write_text(
            json.dumps({"ref": ref, "sha256": sha256}) + "\n",
            encoding="utf-8",
        )
        logger.info("jinn: installed skill %s from %s (via jinn-layer skills install)", slug, ref)
        return str(target / "SKILL.md")
    except (json.JSONDecodeError, KeyError, ValueError, OSError) as exc:
        logger.debug("jinn: skills install CLI path failed for %s: %s", ref, exc)
        return None
    finally:
        if not moved and staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


def _install_via_corpus_get(ref: str, runner: jinn_layer.Runner) -> str:
    code, out = jinn_layer.run(["corpus", "get", ref, "--json"], runner)
    if code != 0:
        raise ValueError(f"corpus get failed: {out}")
    record = json.loads(out)
    extracted = extract_skill(record, ref)
    if extracted is None:
        raise ValueError(
            f"record carries no skill (neither a {SKILL_ARTIFACT_TYPE} artifact nor the seeded trace shape)"
        )
    target = skills_dir() / extracted.slug
    path = _write_skill_tree(target, extracted, ref)
    logger.info("jinn: installed skill %s from %s (%s)", extracted.slug, ref, extracted.shape)
    return path


def install(ref: str, runner: Optional[jinn_layer.Runner] = None) -> str:
    """Install a corpus-published skill by ref. Returns the install path."""
    runner = runner or jinn_layer._default_runner
    cli_path = _install_via_cli(ref, runner)
    if cli_path is not None:
        return cli_path
    return _install_via_corpus_get(ref, runner)


def list_installed() -> List[Dict[str, str]]:
    """Jinn-installed skills only (those carrying the .jinn-ref marker)."""
    directory = skills_dir()
    if not directory.exists():
        return []
    out: List[Dict[str, str]] = []
    for child in sorted(directory.iterdir()):
        marker = child / MARKER_FILE
        if not (child.is_dir() and marker.exists() and (child / "SKILL.md").exists()):
            continue
        try:
            ref = str(json.loads(marker.read_text(encoding="utf-8")).get("ref", ""))
        except Exception:
            ref = ""
        out.append({"slug": child.name, "ref": ref})
    return out


def uninstall(slug: str) -> str:
    """Remove a jinn-installed skill. Refuses anything without the marker."""
    target = skills_dir() / _sanitise_slug(slug)
    if not target.is_dir():
        raise ValueError(f"no installed skill named {slug!r}")
    if not (target / MARKER_FILE).exists():
        raise ValueError(
            f"{slug!r} was not installed by jinn (no {MARKER_FILE} marker) — refusing to remove it"
        )
    shutil.rmtree(target)
    logger.info("jinn: uninstalled skill %s", slug)
    return str(target)
