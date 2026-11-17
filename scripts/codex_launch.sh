#!/usr/bin/env bash
# Routes cold sub-worker requests through the QuadGate local proxy while guarding shell mutations
export OPENAI_BASE_URL="http://127.0.0.1:8788/v1"
quadgate exec-codex -- "$@"
