# Production deployment behind external Caddy

MonkeyOCR runs as a private backend. A separate Caddy entry server owns the
public IP, TLS certificates, public routing, and edge traffic policy.

```text
Internet
  -> Caddy entry server :443
  -> private network
  -> OCR host private IP :6000 (authenticated API for automation)
  -> OCR host private IP :6080 (internal Web workspace)
  -> Web BFF -> Compose API :8000
  -> Compose network :8888 (vLLM, never published on the host)
```

The application remains the final authentication authority: every public
`/api/v1` request, including artifact downloads, requires the single Bearer
token stored on the OCR host. Caddy must not replace or remove the client's
`Authorization` header.

The internal React workspace is served below `/ocr-ui/`. Caddy protects that
whole route with Basic Auth. Its browser calls only the same-origin Web BFF;
the BFF reads the same Compose secret and adds the Bearer token to private API
requests. The token is never included in JavaScript, browser storage, cookies,
or browser HTTP headers.

## Ownership boundary

This repository owns:

- the API, vLLM, and Web containers;
- the private API and Web host bindings;
- model and result mounts;
- the Bearer token and application request limits;
- internal liveness and readiness endpoints.

The entry-server deployment owns:

- the public IP and HTTPS certificate lifecycle;
- public Caddy configuration;
- routing from `/api/v1/*` to the OCR host;
- public rate limiting, connection limiting, access logs, and alerts;
- any private overlay network used between the two servers.

No Nginx, Certbot, or ACME lifecycle is installed by this repository.

## Prerequisites

- An OCR host with Linux x86_64, Docker Compose, the NVIDIA Container Toolkit,
  an Ampere/Ada NVIDIA GPU, and a compatible 550-series or newer driver.
- A Caddy entry server with private network reachability to the OCR host.
- An exact private IPv4 address on the OCR host to which Docker can bind.
- A host firewall and cloud security group that allow the selected API and Web
  ports only from the Caddy server's private IP.
- A protected private link. If the ordinary private network is not trusted,
  use WireGuard, Tailscale, or another encrypted overlay; plain HTTP exposes
  the Bearer token to anyone able to observe that link.

The production containers use the CUDA 12.8 wheel/toolkit stack through CUDA
12.x minor-version compatibility. The vLLM container alone retains the
toolchain required for runtime kernel JIT.

## Initialize the OCR host

```bash
scripts/compose.sh init
```

This creates ignored runtime configuration from the tracked examples and
generates a mode-0600 token at
`dotenv/secrets/monkeyocr_api_token`. Configure these files:

- `dotenv/.env.compose`: private host binding, host paths, UID/GID, profile,
  and optional immutable image references;
- `dotenv/.env.api`: container-side API limits and vLLM/model locations;
- `dotenv/.env.vllm`: vLLM model and scheduling settings;
- `dotenv/.env.web`: Web BFF queue, session, retention, and upload limits;
- `dotenv/.env.build`: build-only proxy and Python index settings.

For example, if the OCR host is `10.0.0.20` and Caddy should connect to port
`6000`, set:

```dotenv
# dotenv/.env.compose
MONKEYOCR_API_BIND_ADDRESS=10.0.0.20
MONKEYOCR_API_HOST_PORT=6000
MONKEYOCR_WEB_BIND_ADDRESS=10.0.0.20
MONKEYOCR_WEB_HOST_PORT=6080
```

Keep the container listener unchanged:

```dotenv
# dotenv/.env.api
MONKEYOCR_API_HOST=0.0.0.0
MONKEYOCR_API_PORT=8000
```

`MONKEYOCR_API_HOST_PORT` selects the OCR host port; it does not change the
port inside the API container. Never use `0.0.0.0` as the host binding unless
the host has no stable private address and an independently verified firewall
provides the same isolation.

Keep the token file owned by the UID configured in `.env.compose` so the
non-root API and Web containers can read the Compose secret. Keep
`MONKEYOCR_WEB_COOKIE_PATH=/ocr-ui` and
`MONKEYOCR_WEB_COOKIE_SECURE=true` for the Caddy deployment.

## Download model weights

The model download command uses the research extra because model-hub clients
are intentionally excluded from the production images:

```bash
uv run --no-dev --extra research monkeyocr-model -n MonkeyOCRv2-B-Parsing
```

The default layout is:

```text
model_weight/
└── MonkeyOCRv2-B-Parsing/
```

## Pull and start immutable images

For production, copy the API, vLLM, and Web digests from their completed
GitHub Actions summaries and set all three references:

```dotenv
MONKEYOCR_PROFILE=standard
MONKEYOCR_API_IMAGE=ghcr.io/l1ndenbaum/monkey-ocr-v2@sha256:<api-digest>
MONKEYOCR_VLLM_IMAGE=ghcr.io/l1ndenbaum/monkey-ocr-v2@sha256:<vllm-digest>
MONKEYOCR_WEB_IMAGE=ghcr.io/l1ndenbaum/monkey-ocr-v2@sha256:<web-digest>
```

Use `@sha256:...` for a digest, not `:sha256:...`. If GHCR is private, first
log in with a token having only `read:packages`.

```bash
scripts/compose.sh config --quiet
scripts/compose.sh pull
scripts/compose.sh up -d --no-build
scripts/compose.sh ps
scripts/compose.sh logs -f vllm api web
```

Docker Hub registry mirrors do not accelerate `ghcr.io`. Build proxy settings
in `.env.build` affect Dockerfile steps only, not daemon image pulls.

## Verify the private backend

Run these checks on the OCR host using its configured private binding and host
port:

```bash
OCR_PRIVATE_URL=http://10.0.0.20:6000

curl -fsS "$OCR_PRIVATE_URL/internal/health/ready"

TOKEN=$(<dotenv/secrets/monkeyocr_api_token)
curl -fsS \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@/path/to/test.png" \
  "$OCR_PRIVATE_URL/api/v1/ocr/text"
```

Also test the readiness URL from the Caddy host. It should be reachable over
the private link but must not be publicly routed:

```bash
curl -fsS http://10.0.0.20:6080/internal/health/ready
```

Confirm from an unrelated private host that both OCR host ports are rejected
by the firewall.

## Configure the external Caddy server

The Caddyfile belongs on the entry server or in its own infrastructure
repository. Generate one bcrypt hash with `caddy hash-password`, expose it to
the Caddy systemd service as `OCR_UI_PASSWORD_HASH`, and adapt the tracked
[`Caddyfile.example`](../infrastructure/caddy/Caddyfile.example). The essential
route boundary is:

```caddyfile
https://203.0.113.10 {
    redir /ocr-ui /ocr-ui/ 308

    handle_path /ocr-ui/* {
        basic_auth {
            ocr {$OCR_UI_PASSWORD_HASH}
        }

        request_body {
            max_size 55MiB
        }

        reverse_proxy http://10.0.0.20:6080 {
            health_uri /internal/health/ready
            health_interval 30s
            health_timeout 5s
        }
    }

    handle /api/v1/* {
        request_body {
            max_size 55MiB
        }

        reverse_proxy http://10.0.0.20:6000 {
            health_uri /internal/health/ready
            health_interval 30s
            health_timeout 5s
        }
    }

    handle {
        respond 404
    }
}
```

Replace all example addresses. This exposes only the versioned automation API
and the Basic-Auth-protected workspace;
active health checks use the private upstream directly. `handle_path` removes
`/ocr-ui` before proxying, while the frontend was built with `/ocr-ui/` as its
public asset base. Standard Caddy `reverse_proxy` behavior retains incoming
headers, including `Authorization`; if the entry deployment defines
`header_up`, verify that it does not delete or overwrite that header. See the
[Caddy reverse proxy documentation](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy).

Apply rate and connection limits at Caddy, its WAF, or the surrounding edge
platform according to that server's installed modules. The API independently
enforces its 50 MiB upload limit, PDF page limit, and global OCR concurrency
limit.

The Web service is intentionally single-replica and ephemeral. It runs one OCR
worker by default, isolates jobs with an HttpOnly same-site session cookie, and
removes copied result files after six hours. A process restart preserves files
until cleanup but forgets the in-memory job list. Horizontal replicas require
an explicit shared queue and session/job store and are outside this deployment
mode.

## Verify the public boundary

```bash
PUBLIC_BASE_URL=https://203.0.113.10
TOKEN=$(<dotenv/secrets/monkeyocr_api_token)

curl -i -X POST "$PUBLIC_BASE_URL/api/v1/parse"
curl -fsS \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@/path/to/test.png" \
  "$PUBLIC_BASE_URL/api/v1/ocr/text"
curl -i "$PUBLIC_BASE_URL/internal/health/ready"
curl -I "$PUBLIC_BASE_URL/ocr-ui/"
curl -u 'ocr:your-password' -I "$PUBLIC_BASE_URL/ocr-ui/"
```

The unauthenticated API request must return HTTP 401 with
`WWW-Authenticate: Bearer`; the authenticated OCR request must succeed; the
public health request must return 404.
The Web request must challenge with HTTP Basic Auth when no credentials are
provided and must load the workspace after valid credentials are supplied.

## Apply this configuration-only change

Changing the host binding does not require a new image. After updating the
repository and `.env.compose`, recreate the API container so Docker replaces
the published port:

```bash
git pull --ff-only
scripts/compose.sh config --quiet
scripts/compose.sh up -d --no-build --force-recreate api web
scripts/compose.sh ps
```

Because `web` depends on the healthy API and `api` depends on healthy vLLM,
Compose verifies the chain without rebuilding or repulling an image. Update
and reload the Caddy configuration separately on the entry server.

## Rotate the Bearer token

```bash
umask 077
openssl rand -hex 32 | tr -d '\n' >dotenv/secrets/monkeyocr_api_token
chmod 600 dotenv/secrets/monkeyocr_api_token
scripts/compose.sh restart api web
```

The API and Web BFF read the token once at startup and fail closed if the
secret is missing, short, malformed, or unreadable. Rotation invalidates the
previous token after both services restart.

## Rollback

To make the backend local-only again, set:

```dotenv
MONKEYOCR_API_BIND_ADDRESS=127.0.0.1
MONKEYOCR_WEB_BIND_ADDRESS=127.0.0.1
```

Then recreate the API and Web containers with
`--no-build --force-recreate api web`.
Container shutdown remains independent of the external entrypoint:

```bash
scripts/compose.sh down
```
