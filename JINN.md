# Jinn-Hermes — the Jinn harness fork

This repository is a **thin fork** of [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent):
upstream Hermes plus the Jinn layer pre-wired. Spec: `spec/2026-07-02-jinn-harness-network.md`
in [Jinn-Network/mono](https://github.com/Jinn-Network/mono) (decisions D1/D2), issue #1312.

## What the fork adds — one integration surface

The entire Jinn layer lives in **three paths**; no upstream file is modified:

| Path | What it is |
|---|---|
| `plugins/jinn/` | The integration surface: first-run consent flow, capture buffer, `jinn-layer` subprocess wrapper, `/jinn` + `/corpus` slash commands |
| `tests/plugins/test_jinn_plugin.py` | Consent-gating integration tests |
| `JINN.md` | This document |

Everything that touches scrubbing, consent conversion, publishing, anchoring,
the ledger or the corpus lives in the **`@jinn-network/harness-layer`
package** (the `jinn-layer` CLI from `@jinn-network/client`), not in fork
code. The plugin shells out to it. That is the thin-fork discipline: the same
layer becomes the plugin for other harnesses, and upstream merges stay cheap.

## Behaviour

- **Consent is OFF by default.** `unset` and `declined` both mean: nothing is
  buffered, nothing is written, nothing leaves the machine. The harness works
  fully as a corpus **reader** either way. Run `/jinn consent` to decide; the
  flow's safe default (bare Enter) is decline.
- **Capture is harness task traces only** — the plugin buffers the session's
  first user message and its tool calls, never anything outside a task.
- **Scrub is fail-closed and happens locally** inside `jinn-layer` before
  anything can publish. If scrubbing can't finish, nothing sends.
- **Preview-gated first publish**: after accepting, nothing publishes until
  you run `/jinn preview` once and see the exact outgoing envelope.
- **Per-task veto**: `/jinn veto` withholds the current task; the ledger
  records `vetoed (local only)`.
- **Publish failure retains locally**: the assembled trace stays under
  `$HERMES_HOME/jinn/pending/` and the ledger/UI shows
  `publish failed — retained locally`.
- `/jinn ledger` — what left this machine, with anchor links.
- `/corpus <query>` — in-session corpus search.
- **`/jinn skills install <ref>`** — install a corpus-published skill into
  Hermes's native skills: `corpus get` → sha256 verification → SKILL.md
  written to `$HERMES_HOME/skills/<slug>/`; Hermes's loader takes over.
  Consuming is always allowed — no consent needed to install (consent gates
  contributing, never reading). `/jinn skills list` / `uninstall <slug>`
  manage jinn-installed skills only (a `.jinn-ref` marker fences them; a
  user's own skills are never touched).

Requires the `jinn-layer` CLI on PATH (`npm install -g @jinn-network/client`)
or `JINN_LAYER_BIN` pointing at it. Testnet in v0.

## Upstream-merge procedure (the thin-fork proof)

The fork tracks upstream `main`. To take upstream:

```bash
git remote add upstream https://github.com/NousResearch/hermes-agent.git  # once
git fetch upstream
git checkout jinn-layer
git merge upstream/main
```

**Expected conflicts: none.** The Jinn layer adds files only (`plugins/jinn/`,
`tests/plugins/test_jinn_plugin.py`, `JINN.md`) and modifies zero upstream
files, so a clean upstream merge cannot conflict outside the integration
surface. If a merge ever conflicts on an upstream file, that is a thin-fork
regression — record it in the merge PR and move the offending change into the
plugin or the harness-layer package.

After each upstream merge, run the fork's own gate:

```bash
python3 -m pytest tests/plugins/test_jinn_plugin.py -q
```

## Licence

Upstream hermes-agent is MIT; this fork keeps the upstream `LICENSE` and adds
the Jinn layer under the same terms.
