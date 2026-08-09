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
source "$dotenv_dir/.env.compose"
source "$dotenv_dir/.env.vllm"
set +a

case "$MONKEYOCR_PROFILE" in
    standard) ;;
    *)
        echo "Docker production services support only MONKEYOCR_PROFILE=standard." >&2
        echo "Use the explicit research/DFlash CLI extras outside this Compose stack." >&2
        exit 1
        ;;
esac

legacy_image=${MONKEYOCR_IMAGE:-}
if [[ -z ${MONKEYOCR_API_IMAGE:-} && -z ${MONKEYOCR_VLLM_IMAGE:-} && -n $legacy_image ]]; then
    MONKEYOCR_API_IMAGE=$legacy_image
    MONKEYOCR_VLLM_IMAGE=$legacy_image
fi
MONKEYOCR_API_IMAGE=${MONKEYOCR_API_IMAGE:-monkeyocr:api-standard}
MONKEYOCR_VLLM_IMAGE=${MONKEYOCR_VLLM_IMAGE:-monkeyocr:vllm-standard}
export MONKEYOCR_API_IMAGE MONKEYOCR_VLLM_IMAGE

exec docker compose \
    --project-directory "$repo_root" \
    --env-file "$dotenv_dir/.env.build" \
    --env-file "$dotenv_dir/.env.compose" \
    -f "$repo_root/infrastructure/docker/compose.yml" \
    "$@"
