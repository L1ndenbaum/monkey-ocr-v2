# syntax=docker/dockerfile:1.7

ARG CUDA_BASE_IMAGE=nvidia/cuda:12.9.1-cudnn-devel-ubuntu24.04@sha256:a2e1e2360c85298ac47ec2543b406ab1e8cec42e31ee47e4d32140ebc82e1067
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.12.1@sha256:cf4eedcaa81655197f625739489effcbe71b61ceb1506f332c3facae5deceded

FROM ${UV_IMAGE} AS uv
FROM ${CUDA_BASE_IMAGE} AS runtime

ARG INFERENCE_PROFILE=standard
ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG NO_PROXY=localhost,127.0.0.1,api,vllm
ARG UV_INDEX_URL=https://pypi.org/simple

ENV DEBIAN_FRONTEND=noninteractive \
    PATH=/opt/venv/bin:/opt/python/bin:${PATH} \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON=3.11 \
    UV_PYTHON_INSTALL_DIR=/opt/python

COPY --from=uv /uv /uvx /usr/local/bin/

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    apt-get update \
    && apt-get install --no-install-recommends -y \
        ca-certificates \
        curl \
        libglib2.0-0 \
        libgomp1 \
        tini \
    && update-ca-certificates

WORKDIR /app

RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    uv python install 3.11

COPY pyproject.toml uv.lock README.md LICENSE ./

RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    uv sync --frozen --no-install-project --no-dev \
        --extra inference --extra "${INFERENCE_PROFILE}"

COPY src ./src

RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    uv sync --frozen --no-dev --extra inference --extra "${INFERENCE_PROFILE}" \
    && groupadd --gid 10001 monkeyocr \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin monkeyocr \
    && install -d -o 10001 -g 10001 /var/lib/monkeyocr/results

USER 10001:10001

EXPOSE 8000

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["monkeyocr-api"]
