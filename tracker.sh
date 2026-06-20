#!/usr/bin/env bash
set -euo pipefail

podman run --rm -v "$PWD/data:/app/data:z" portfolio-tracker "$@"
