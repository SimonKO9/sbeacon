#!/usr/bin/env bash
set -euo pipefail

VERSION=$(grep '^version' "$(dirname "$0")/pyproject.toml" | sed 's/version = "\(.*\)"/\1/' | tr -d '\r')
podman run --rm -v "$PWD/data:/app/data:z" -e COLUMNS="$(tput cols)" ghcr.io/simonko9/sbeacon:"$VERSION" "$@"
