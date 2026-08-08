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

http_mode=${MONKEYOCR_HTTP_MODE:-managed}
case "$http_mode" in
    managed | preserve) ;;
    *)
        echo "MONKEYOCR_HTTP_MODE must be either 'managed' or 'preserve'." >&2
        exit 1
        ;;
esac

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

for command in nginx certbot curl envsubst openssl python3 systemctl; do
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

template_vars='${MONKEYOCR_PUBLIC_IP} ${MONKEYOCR_PUBLIC_AUTHORITY} ${MONKEYOCR_ACME_WEBROOT} ${MONKEYOCR_NGINX_UPSTREAM}'

verify_preserved_http_challenge() {
    local challenge_dir probe_name probe_path probe_value response
    challenge_dir="$MONKEYOCR_ACME_WEBROOT/.well-known/acme-challenge"
    probe_name="monkeyocr-install-$(openssl rand -hex 8)"
    probe_path="$challenge_dir/$probe_name"
    probe_value=$(openssl rand -hex 16)
    printf '%s' "$probe_value" >"$probe_path"
    chmod 644 "$probe_path"

    if ! response=$(curl \
        --noproxy '*' \
        --fail \
        --silent \
        --show-error \
        --max-time 10 \
        --header "Host: $MONKEYOCR_PUBLIC_AUTHORITY" \
        "http://127.0.0.1/.well-known/acme-challenge/$probe_name"); then
        rm -f "$probe_path"
        echo "The existing HTTP default server does not expose $MONKEYOCR_ACME_WEBROOT as the ACME webroot." >&2
        echo "Add the documented /.well-known/acme-challenge/ location, reload Nginx, and retry." >&2
        exit 1
    fi
    rm -f "$probe_path"

    if [[ $response != "$probe_value" ]]; then
        echo "The existing HTTP default server returned unexpected content for the ACME challenge path." >&2
        echo "Check its /.well-known/acme-challenge/ location before retrying." >&2
        exit 1
    fi
}

if [[ $http_mode == "managed" ]]; then
    if [[ ${MONKEYOCR_REPLACE_DEFAULT_SERVER:-false} == "true" && -L /etc/nginx/sites-enabled/default ]]; then
        mv /etc/nginx/sites-enabled/default /etc/nginx/sites-enabled/default.disabled-by-monkeyocr
    fi

    envsubst "$template_vars" \
        <"$repo_root/infrastructure/nginx/monkeyocr-http.conf.template" \
        >/etc/nginx/sites-available/monkeyocr.conf
    ln -sfn /etc/nginx/sites-available/monkeyocr.conf /etc/nginx/sites-enabled/monkeyocr.conf
    nginx -t
    systemctl enable --now nginx
    systemctl reload nginx
else
    nginx -t
    systemctl enable --now nginx
    systemctl reload nginx
    verify_preserved_http_challenge
fi

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

if [[ $http_mode == "preserve" ]]; then
    final_template="$repo_root/infrastructure/nginx/monkeyocr-https.conf.template"
else
    final_template="$repo_root/infrastructure/nginx/monkeyocr.conf.template"
fi
envsubst "$template_vars" \
    <"$final_template" \
    >/etc/nginx/sites-available/monkeyocr.conf
ln -sfn /etc/nginx/sites-available/monkeyocr.conf /etc/nginx/sites-enabled/monkeyocr.conf
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

if [[ $http_mode == "preserve" ]]; then
    echo "Preserved the existing HTTP default server; MonkeyOCR owns HTTPS only."
fi
echo "MonkeyOCR is available at https://$MONKEYOCR_PUBLIC_AUTHORITY/api/v1"
