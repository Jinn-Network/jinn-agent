#!/bin/sh
# jinn-agent setup — one-time install (deps, sandboxing, agent core).
# Thin wrapper over the upstream core's installer so no user-facing step
# carries the upstream name.
cd "$(dirname "$0")" && exec ./setup-hermes.sh "$@"
