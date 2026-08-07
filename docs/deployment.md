# Public IP deployment

This deployment keeps TLS termination on the host and runs only the API and
vLLM in Docker. The API binds to `127.0.0.1:8000`; vLLM is reachable only on
the Compose network.

## Prerequisites

- Linux x86_64 with an NVIDIA GPU, current NVIDIA driver, Docker, Docker
  Compose, and the NVIDIA Container Toolkit.
- The public IPv4 or IPv6 address routed directly to the server.
- Firewall ingress for TCP 80 and 443. Do not expose 8000 or 8888.
- Nginx, `envsubst`, OpenSSL, and Certbot 5.4 or newer on the host. The Certbot
  Nginx installer does not issue IP certificates; this repository uses
  `certonly --webroot --ip-address`.
- A proxy listening on host port 9090 when the default build proxy values are
  retained.

On Ubuntu, install Nginx and `envsubst` from apt, then install a current Certbot
release (for example with the official snap). Confirm `certbot --version`
reports at least 5.4 before continuing.

## Initialize and configure

```bash
scripts/compose.sh init
```

Edit these ignored files:

- `dotenv/.env.compose`: host paths, UID/GID, exposed loopback port, and
  `standard` or `dflash` profile.
- `dotenv/.env.api`: API limits and model/vLLM locations inside containers.
- `dotenv/.env.vllm`: vLLM scheduling. Set the draft path when using DFlash.
- `dotenv/.env.build`: build proxy and Python index. The supplied proxy URLs
  already use `host.docker.internal`; no wrapper conversion is performed.
- `dotenv/.env.nginx`: real public IP, ACME email, webroot, and API upstream.

The local Bearer token is generated without a trailing newline at
`dotenv/secrets/monkeyocr_api_token` with mode 0600. On a server, keep this file
owned by the UID configured in `dotenv/.env.compose` so the non-root API
container can read the Compose secret.

Place model directories below the configured model root. The default layout is:

```text
model_weight/
├── MonkeyOCRv2-B-Parsing/
└── MonkeyOCRv2-B-Parsing-DFlash/  # DFlash only
```

## Start the GPU services

```bash
scripts/compose.sh config --quiet
scripts/compose.sh build
scripts/compose.sh up -d
scripts/compose.sh ps
scripts/compose.sh logs -f vllm api
```

Verify the loopback API before enabling the public proxy:

```bash
curl -fsS http://127.0.0.1:8000/internal/health/ready

TOKEN=$(<dotenv/secrets/monkeyocr_api_token)
curl -fsS \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@/path/to/test.png" \
  http://127.0.0.1:8000/api/v1/ocr/text
```

The first health endpoint is intentionally internal. Nginx never proxies it.

## Obtain the IP certificate and install Nginx

Keep port 80 reachable: IP certificates use HTTP-01 and must be renewed
frequently. After editing `dotenv/.env.nginx`, run:

```bash
sudo scripts/install-host-nginx.sh
```

For a first dry run, set `MONKEYOCR_ACME_STAGING=true`; the resulting
certificate is deliberately untrusted. Set it back to `false` and rerun for
the production certificate.

The installer performs this sequence:

1. Validates the IP and Certbot version.
2. Installs an HTTP-only default server that exposes only the ACME webroot.
3. Requests a `shortlived` IP certificate with Certbot webroot mode.
4. Replaces the bootstrap site with the HTTPS reverse proxy.
5. Installs and starts `monkeyocr-cert-renew.timer`.

Direct-IP TLS has no domain fallback. The Nginx TLS listener is therefore the
default server and always presents the IP certificate, including when a client
does not send useful SNI. If the public IP changes, update `.env.nginx`, obtain
a new certificate for the new literal address, and update clients.

## Verify the public boundary

```bash
TOKEN=$(<dotenv/secrets/monkeyocr_api_token)

curl -i "https://PUBLIC_IP/api/v1/parse"
curl -fsS \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@/path/to/test.png" \
  "https://PUBLIC_IP/api/v1/ocr/text"

curl -fsS "https://PUBLIC_IP/internal/health/ready" || true
```

The first request must return 401 with `WWW-Authenticate: Bearer`; the second
must return an envelope with `internal_code=SUCCESS`; the health request must
not be publicly routed.

## Renewal and alert checks

Let’s Encrypt IP certificates are valid for roughly six days. The installed
timer runs every 12 hours with randomized delay:

```bash
systemctl status monkeyocr-cert-renew.timer
sudo systemctl start monkeyocr-cert-renew.service
journalctl -u monkeyocr-cert-renew.service -n 100 --no-pager
sudo certbot certificates
```

After renewal, Certbot tests and reloads Nginx. The renewal script exits with a
failure and writes a critical syslog message if the certificate has less than
48 hours remaining; connect systemd failures or syslog critical events to the
server's existing alert channel.

## Rotate the Bearer token

```bash
umask 077
openssl rand -hex 32 | tr -d '\n' >dotenv/secrets/monkeyocr_api_token
chmod 600 dotenv/secrets/monkeyocr_api_token
scripts/compose.sh restart api
```

The API reads the token once at startup and fails closed if the secret is
missing, short, malformed, or unreadable. Rotating it invalidates the previous
token after the API restart.

## Host proxy rollback

The installer preserves Debian's default-site symlink as
`/etc/nginx/sites-enabled/default.disabled-by-monkeyocr` when replacement is
enabled. To remove the MonkeyOCR host proxy, disable its timer, remove the
MonkeyOCR site symlink, restore that default symlink if needed, then run
`nginx -t` before reloading Nginx. Container rollback is independent:

```bash
scripts/compose.sh down
```
