#!/bin/sh
# Build and publish the repository's image using the current Docker login.
set -eu

case "${1-}" in
    -h|--help)
        cat <<'EOF'
Usage: publish-dockerhub.sh [docker build options]

Build and push macjoker/scan-to-mail:latest from the repository root.
First authenticate with: docker login --username macjoker

Examples:
  ./scripts/publish-dockerhub.sh
  ./scripts/publish-dockerhub.sh --platform linux/amd64
  ./scripts/publish-dockerhub.sh --tag macjoker/scan-to-mail:1.0.0

Extra options are forwarded to docker build. An extra --tag publishes both
latest and that tag. The build context is always this repository.
EOF
        exit 0
        ;;
esac

if ! command -v docker >/dev/null 2>&1; then
    echo "Docker is required to build and publish the image." >&2
    exit 127
fi

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$project_dir"
exec docker build -t macjoker/scan-to-mail:latest --push "$@" .
