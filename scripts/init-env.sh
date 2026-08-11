#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
dotenv_dir="$repo_root/dotenv"

for role in build compose api vllm web; do
    destination="$dotenv_dir/.env.$role"
    if [[ ! -e "$destination" ]]; then
        cp "$destination.example" "$destination"
    fi
done

secret_dir="$dotenv_dir/secrets"
secret_file="$secret_dir/monkeyocr_api_token"
install -d -m 700 "$secret_dir"
if [[ ! -s "$secret_file" ]]; then
    umask 077
    openssl rand -hex 32 | tr -d '\n' >"$secret_file"
fi
chmod 600 "$secret_file"

install -d "$repo_root/model_weight" "$repo_root/output/api" "$repo_root/output/web"

echo "Initialized local dotenv files and Bearer token under $dotenv_dir"
