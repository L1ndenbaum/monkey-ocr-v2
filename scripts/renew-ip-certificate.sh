#!/usr/bin/env bash
set -euo pipefail

: "${MONKEYOCR_PUBLIC_IP:?MONKEYOCR_PUBLIC_IP is required}"

certbot renew \
    --cert-name "$MONKEYOCR_PUBLIC_IP" \
    --deploy-hook "nginx -t && systemctl reload nginx" \
    --quiet

certificate="/etc/letsencrypt/live/$MONKEYOCR_PUBLIC_IP/fullchain.pem"
if ! openssl x509 -checkend 172800 -noout -in "$certificate"; then
    logger -p daemon.crit -t monkeyocr-cert-renew \
        "IP certificate for $MONKEYOCR_PUBLIC_IP expires in less than 48 hours"
    exit 1
fi
