# jinn-agent — the Jinn harness

**jinn-agent** is an open coding harness plugged into the Jinn network: it
reads from the public corpus, and (with consent) contributes scrubbed task
traces back to it. The product name is `jinn-agent`, everywhere a human
looks; this repository is technically a thin fork of an upstream agent core
([NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent))
— that name is provenance, not product, and no user-facing surface should
use it. Spec: `spec/2026-07-02-jinn-harness-network.md` in
[Jinn-Network/mono](https://github.com/Jinn-Network/mono) (decisions D1/D2),
issue #1312.

## Run

```bash
./setup.sh            # one-time: deps, sandboxing, agent core
bin/jinn-agent        # start the harness
```

In-session: `/jinn consent` to decide about contributing (default: decline —
reader only), `/corpus <query>` to search the network's knowledge,
`/jinn ledger` for the receipt trail of anything that left your machine.

## Coexists with a stock upstream install

Already running the upstream agent? No conflict:

- **Separate state home.** jinn-agent defaults to `~/.jinn-agent` (config,
  auth, skills, memories, sessions) — it never reads or writes `~/.hermes`.
  Corpus-installed skills therefore never leak into a stock install, and
  version skew between the two cannot corrupt shared state. Override with
  `JINN_AGENT_HOME`, or set `HERMES_HOME` explicitly to share state with a
  stock install on purpose.
- **Repo-local install.** `setup.sh` builds a venv inside this repo — no
  global package, so an existing upstream install (and its `hermes` command)
  is untouched.
- **One caution:** don't hand both installs the same messaging-platform bot
  tokens and run both gateways — the platform will get duplicate replies.
  That's true of any two agent instances, not specific to this fork.

Provider keys go in the jinn-agent home on first run (`~/.jinn-agent/.env`).

## What the Jinn layer adds — one integration surface

The entire Jinn layer lives in **three paths**; no upstream file is modified:

| Path | What it is |
|---|---|
| `bin/jinn-agent`, `setup.sh` | The human-facing entrypoints (run + one-time setup) |
| `plugins/jinn/` | The integration surface: first-run consent flow, capture buffer + payload-agnostic pickup, `jinn-layer` subprocess wrapper, agent tools, `/jinn` + `/corpus` slash commands |
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
- **Payload-agnostic auto-pickup** — at task start the harness derives the
  task's distribution, looks up the corpus, and decides per candidate by
  **verification tier, not human keystroke**: payloads at or above the
  configured threshold (default `evaluator-verified`) are adopted
  automatically — verification under bond is the trust gate; anything below
  is surfaced to the agent as injected context, install stays deliberate.
  Adopters are a registry keyed by payload type (`skill` ships in v0;
  loadout recommendations and full loadouts plug in as new adopters — same
  rail, richer payloads). Config: `$HERMES_HOME/jinn/pickup.json`
  (`enabled`, `autoAdoptTier`, `maxCandidates`). Fails open; never
  consent-gated (consuming is always allowed). Today's corpus holds nothing
  verified, so today this runs suggest-only — honestly.
- **Agent tools `corpus_search` / `corpus_fetch`** — the agent itself can
  search the corpus by content and read a record's full text mid-task
  (hash-verified), with or without installing anything.
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
