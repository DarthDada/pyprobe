#!/bin/bash
# Enable the tracked git hooks (原 TODO §6-1): point core.hooksPath at .githooks
# so pre-commit/pre-push run through scripts/. Local-only and reversible.
set -e
cd "$(dirname "$0")/.."

git config core.hooksPath .githooks
chmod +x .githooks/*
echo "git hooks enabled (core.hooksPath=.githooks)"
echo "disable with: git config --unset core.hooksPath"
