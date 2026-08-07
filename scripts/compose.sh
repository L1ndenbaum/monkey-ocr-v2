#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
dotenv_dir="$repo_root/dotenv"

if [[ ${1:-} == "init" ]]; then
    exec "$repo_root/scripts/init-env.sh"
fi

for required in .env.build .env.compose .env.api .env.vllm; do
    if [[ ! -f "$dotenv_dir/$required" ]]; then
        echo "Missing $dotenv_dir/$required; run scripts/compose.sh init first." >&2
        exit 1
    fi
done

set -a
source "$dotenv_dir/.env.build"
source "$dotenv_dir/.env.compose"
source "$dotenv_dir/.env.vllm"
set +a

case "$MONKEYOCR_PROFILE" in
    standard) ;;
    dflash)
        if [[ -z ${MONKEYOCR_DRAFT_MODEL_PATH:-} ]]; then
            echo "DFlash profile requires MONKEYOCR_DRAFT_MODEL_PATH in dotenv/.env.vllm." >&2
            exit 1
        fi
        ;;
    *)
        echo "MONKEYOCR_PROFILE must be standard or dflash." >&2
        exit 1
        ;;
esac

exec docker compose \
    --project-directory "$repo_root" \
    --env-file "$dotenv_dir/.env.compose" \
    -f "$repo_root/infrastructure/docker/compose.yml" \
    "$@"
