#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
DOCKERFILE="docker/Dockerfile"

usage() {
    cat <<EOF
Usage: $(basename "$0") <mode> [options]

Modes:
  dev            Build the development image (yuki:dev)
  prod           Build the production image (yuki:<version>, yuki:latest)

Options (prod only):
  --tar          Export the image with docker save (yuki-<version>.tar)
  --nightly      Nightly naming: yuki-nightly:0.0.<date>-1, :nightly, :latest
EOF
}

mode="${1:-}"
if [ $# -gt 0 ]; then shift; fi

tar_export=false
nightly=false
for arg in "$@"; do
    case "$arg" in
        --tar) tar_export=true ;;
        --nightly) nightly=true ;;
        *) usage >&2; exit 1 ;;
    esac
done

cd "${REPO_ROOT}"

case "$mode" in
    dev)
        if $tar_export || $nightly; then
            echo "error: --tar/--nightly are only valid with prod" >&2
            exit 1
        fi
        docker build -f "${DOCKERFILE}" --target dev -t yuki:dev .
        echo "Built yuki:dev"
        ;;
    prod)
        if $nightly; then
            date_tag="$(date +%Y%m%d)"
            ref="yuki-nightly:0.0.${date_tag}-1"
            tags=(-t "${ref}" -t yuki-nightly:nightly -t yuki-nightly:latest)
            tar_name="yuki-nightly-0.0.${date_tag}-1.tar"
        else
            version="$(sed -n 's/^version = "\([^"]*\)".*/\1/p' pyproject.toml | head -n1)"
            if [ -z "$version" ]; then
                echo "error: could not read version from pyproject.toml" >&2
                exit 1
            fi
            ref="yuki:${version}"
            tags=(-t "${ref}" -t yuki:latest)
            tar_name="yuki-${version}.tar"
        fi
        docker build -f "${DOCKERFILE}" --target prod "${tags[@]}" .
        echo "Built ${ref}"
        if $tar_export; then
            docker save -o "${tar_name}" "${ref}"
            echo "Wrote ${tar_name}"
        fi
        ;;
    *)
        usage >&2
        exit 1
        ;;
esac
