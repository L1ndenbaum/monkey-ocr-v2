# Repository working rules

These rules apply to the whole repository.

## Architecture

This repository is the MonkeyOCR bounded context. Production Python code lives
under `src/monkeyocr` and follows a layer-first DDD layout:

- `domain/`: framework-free OCR concepts, value objects, policies, and errors.
- `application/`: use cases, commands, DTOs, and ports required by the use cases.
- `infrastructure/`: adapters for vLLM, files, PDF/image processing, model
  integration, training, and other external systems.
- `interface/`: inbound protocols and composition roots. HTTP v1 belongs in
  `interface/http/v1`; CLI entrypoints belong in `interface/cli`.

Dependencies point inward: `interface -> application -> domain`.
`infrastructure` implements application ports and may depend on `application`
and `domain`. The domain must not import FastAPI, Pydantic, Torch, vLLM, Pillow,
filesystem, networking, or environment configuration. HTTP handlers only map
transport input/output and invoke application use cases.

The vendored ms-swift checkout belongs at
`src/monkeyocr/infrastructure/training/vendor/ms-swift`. It keeps its own build
metadata and is excluded from the root package, production image, Ruff, Mypy,
and root tests. Do not couple production imports to vendored training code.

## Configuration and security

- Commit templates as `dotenv/*.example`; never commit real `dotenv/.env*`
  files or anything below `dotenv/secrets/`.
- The public API uses one Bearer token loaded from a secret file and fails
  closed if it is missing or invalid. Never log tokens or authorization headers.
- Public HTTP routes are versioned below `/api/v1`. Health routes are internal.
- Result files are not statically exposed; protected artifact download is the
  only public result-file path.
- Keep build-time proxy settings separate from runtime configuration. Linux
  Docker builds and services that need the host use
  `host.docker.internal:host-gateway`.

## Code and tests

- Python is 3.11. Add type annotations to changed production code.
- Tests mirror production boundaries under `tests/` and must cover security,
  input limits, error envelopes, and changed use-case behavior.
- Keep offline/research functionality available through explicit extras and CLI
  commands; do not pull it into the production API dependency graph.
- Use Conventional Commit messages and keep every commit to one logical change.

After every logical change, and before committing it, run all of:

```text
uv run ruff check .
uv run ruff format --check .
uv run mypy src/monkeyocr
uv run pytest
git diff --check
```

Fix every failure before committing. Infrastructure-only changes also require
their targeted validation (for example `docker compose config`, Docker build
checks, or `nginx -t`) when the necessary tool is available. Commit only after
the required checks pass.
