#!/usr/bin/env bash
set -euo pipefail

VERSION=$(grep '^version' "$(dirname "$0")/pyproject.toml" | sed 's/version = "\(.*\)"/\1/')
podman run --rm -v "$PWD/data:/app/data:z" ghcr.io/simonko9/sbeacon:"$VERSION" "$@"
