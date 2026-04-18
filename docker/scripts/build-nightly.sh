#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

DATE_TAG=$(date +%Y%m%d)
VERSION="0.0.${DATE_TAG}-1"
IMAGE_NAME="yuki-nightly"
FULL_TAG="${IMAGE_NAME}:${VERSION}"

cd "${PROJECT_ROOT}"

echo "Building ${FULL_TAG}..."
docker build -f docker/Dockerfile -t "${FULL_TAG}" -t "${IMAGE_NAME}:nightly" -t "${IMAGE_NAME}:latest" .

echo "Build complete: ${FULL_TAG}"
echo "Also tagged as: ${IMAGE_NAME}:nightly, ${IMAGE_NAME}:latest"

# Optional: save to tar
# docker save -o "${IMAGE_NAME}-${VERSION}.tar" "${FULL_TAG}"
