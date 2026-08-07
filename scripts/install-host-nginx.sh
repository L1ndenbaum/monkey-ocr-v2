#!/usr/bin/env bash
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Run this installer as root." >&2
    exit 1
fi

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
nginx_env=${1:-$repo_root/dotenv/.env.nginx}
if [[ ! -f "$nginx_env" ]]; then
    echo "Missing $nginx_env; initialize and edit dotenv/.env.nginx first." >&2
    exit 1
fi

set -a
source "$nginx_env"
set +a

: "${MONKEYOCR_PUBLIC_IP:?MONKEYOCR_PUBLIC_IP is required}"
: "${MONKEYOCR_ACME_EMAIL:?MONKEYOCR_ACME_EMAIL is required}"
: "${MONKEYOCR_ACME_WEBROOT:?MONKEYOCR_ACME_WEBROOT is required}"
: "${MONKEYOCR_NGINX_UPSTREAM:?MONKEYOCR_NGINX_UPSTREAM is required}"

if [[ $MONKEYOCR_PUBLIC_IP == "203.0.113.10" || $MONKEYOCR_ACME_EMAIL == "admin@example.com" ]]; then
    echo "Replace the example public IP and ACME email before installation." >&2
    exit 1
fi

MONKEYOCR_PUBLIC_AUTHORITY=$(python3 - "$MONKEYOCR_PUBLIC_IP" <<'PY'
import ipaddress
import sys

address = ipaddress.ip_address(sys.argv[1])
print(f"[{address}]" if address.version == 6 else address)
PY
)
export MONKEYOCR_PUBLIC_AUTHORITY

for command in nginx certbot envsubst openssl python3 systemctl; do
    if ! command -v "$command" >/dev/null 2>&1; then
        echo "Required command is missing: $command" >&2
        exit 1
    fi
done

certbot_version=$(certbot --version 2>&1 | awk '{print $2}')
if [[ $(printf '%s\n' "5.4.0" "$certbot_version" | sort -V | head -n1) != "5.4.0" ]]; then
    echo "Certbot 5.4.0 or newer is required for webroot IP certificates." >&2
    exit 1
fi

install -d -m 755 "$MONKEYOCR_ACME_WEBROOT/.well-known/acme-challenge"
install -d -m 755 /etc/nginx/sites-available /etc/nginx/sites-enabled /etc/monkeyocr

if [[ ${MONKEYOCR_REPLACE_DEFAULT_SERVER:-false} == "true" && -L /etc/nginx/sites-enabled/default ]]; then
    mv /etc/nginx/sites-enabled/default /etc/nginx/sites-enabled/default.disabled-by-monkeyocr
fi

template_vars='${MONKEYOCR_PUBLIC_IP} ${MONKEYOCR_PUBLIC_AUTHORITY} ${MONKEYOCR_ACME_WEBROOT} ${MONKEYOCR_NGINX_UPSTREAM}'
envsubst "$template_vars" \
    <"$repo_root/infrastructure/nginx/monkeyocr-http.conf.template" \
    >/etc/nginx/sites-available/monkeyocr.conf
ln -sfn /etc/nginx/sites-available/monkeyocr.conf /etc/nginx/sites-enabled/monkeyocr.conf
nginx -t
systemctl enable --now nginx
systemctl reload nginx

certbot_args=(
    certonly
    --non-interactive
    --agree-tos
    --email "$MONKEYOCR_ACME_EMAIL"
    --preferred-profile shortlived
    --webroot
    --webroot-path "$MONKEYOCR_ACME_WEBROOT"
    --ip-address "$MONKEYOCR_PUBLIC_IP"
    --cert-name "$MONKEYOCR_PUBLIC_IP"
    --deploy-hook "nginx -t && systemctl reload nginx"
)
if [[ ${MONKEYOCR_ACME_STAGING:-false} == "true" ]]; then
    certbot_args+=(--staging)
fi
certbot "${certbot_args[@]}"

envsubst "$template_vars" \
    <"$repo_root/infrastructure/nginx/monkeyocr.conf.template" \
    >/etc/nginx/sites-available/monkeyocr.conf
nginx -t
systemctl reload nginx

install -m 755 "$repo_root/scripts/renew-ip-certificate.sh" \
    /usr/local/sbin/monkeyocr-renew-ip-certificate
install -m 644 "$nginx_env" /etc/monkeyocr/nginx.env
install -m 644 "$repo_root/infrastructure/nginx/systemd/monkeyocr-cert-renew.service" \
    /etc/systemd/system/monkeyocr-cert-renew.service
install -m 644 "$repo_root/infrastructure/nginx/systemd/monkeyocr-cert-renew.timer" \
    /etc/systemd/system/monkeyocr-cert-renew.timer
systemctl daemon-reload
systemctl enable --now monkeyocr-cert-renew.timer

echo "MonkeyOCR is available at https://$MONKEYOCR_PUBLIC_IP/api/v1"
