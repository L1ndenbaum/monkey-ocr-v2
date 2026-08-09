ARG CUDA_RUNTIME_IMAGE=nvidia/cuda:12.8.1-runtime-ubuntu22.04@sha256:fcbbd60a5ad3db3a1c7375bf14546b369b54064c513224310b2026df50c7a9bd
ARG CUDA_DEVEL_IMAGE=nvidia/cuda:12.8.1-devel-ubuntu22.04@sha256:6617a625f4090c76c545a0e7d63f2e441718ef9af7f4efe7dd1242a29e289fd7
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.12.1@sha256:cf4eedcaa81655197f625739489effcbe71b61ceb1506f332c3facae5deceded

FROM ${UV_IMAGE} AS uv

FROM ${CUDA_RUNTIME_IMAGE} AS python-builder

ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG NO_PROXY=localhost,127.0.0.1,api,vllm
ARG UV_INDEX_URL=https://pypi.org/simple

ENV UV_LINK_MODE=copy \
    UV_PYTHON=3.11 \
    UV_PYTHON_INSTALL_DIR=/opt/python

COPY --from=uv /uv /uvx /usr/local/bin/

RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    uv python install 3.11

FROM python-builder AS heavy-dependencies

COPY infrastructure/docker/requirements /tmp/requirements

RUN uv venv /tmp/heavy-venv
RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    uv pip install --python /tmp/heavy-venv/bin/python --no-deps \
        --target /layers/cuda-core \
        --requirement /tmp/requirements/standard-cuda-core.txt
RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    uv pip install --python /tmp/heavy-venv/bin/python --no-deps \
        --target /layers/cuda-math \
        --requirement /tmp/requirements/standard-cuda-math.txt
RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    uv pip install --python /tmp/heavy-venv/bin/python --no-deps \
        --target /layers/cuda-solver \
        --requirement /tmp/requirements/standard-cuda-solver.txt
RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    uv pip install --python /tmp/heavy-venv/bin/python --no-deps \
        --target /layers/torch \
        --requirement /tmp/requirements/standard-torch.txt

FROM python-builder AS api-environment

ENV UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /app

COPY pyproject.toml uv.lock ./

RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    uv sync --frozen --no-install-project --no-dev \
        --extra inference --extra standard-runtime

COPY README.md LICENSE ./
COPY src ./src

RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    uv sync --frozen --no-dev --no-editable \
        --extra inference --extra standard-runtime

FROM api-environment AS api-thin-environment

COPY infrastructure/docker/requirements /tmp/sharded-requirements
COPY infrastructure/docker/strip_sharded_packages.py /tmp/strip_sharded_packages.py

RUN /opt/venv/bin/python /tmp/strip_sharded_packages.py \
        --site-packages /opt/venv/lib/python3.11/site-packages \
        --requirement /tmp/sharded-requirements/standard-cuda-core.txt \
        --requirement /tmp/sharded-requirements/standard-cuda-math.txt \
        --requirement /tmp/sharded-requirements/standard-cuda-solver.txt \
        --requirement /tmp/sharded-requirements/standard-torch.txt

FROM python-builder AS vllm-environment

ENV UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /app

COPY pyproject.toml uv.lock ./

RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    uv sync --frozen --no-install-project --no-dev \
        --extra inference --extra standard-runtime --extra standard

COPY README.md LICENSE ./
COPY src ./src

RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    uv sync --frozen --no-dev --no-editable \
        --extra inference --extra standard-runtime --extra standard

FROM vllm-environment AS vllm-thin-environment

COPY infrastructure/docker/requirements /tmp/sharded-requirements
COPY infrastructure/docker/strip_sharded_packages.py /tmp/strip_sharded_packages.py

RUN /opt/venv/bin/python /tmp/strip_sharded_packages.py \
        --site-packages /opt/venv/lib/python3.11/site-packages \
        --requirement /tmp/sharded-requirements/standard-cuda-core.txt \
        --requirement /tmp/sharded-requirements/standard-cuda-math.txt \
        --requirement /tmp/sharded-requirements/standard-cuda-solver.txt \
        --requirement /tmp/sharded-requirements/standard-torch.txt

FROM ${CUDA_RUNTIME_IMAGE} AS api

ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG NO_PROXY=localhost,127.0.0.1,api,vllm

ENV PATH=/opt/venv/bin:/opt/python/bin:${PATH} \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /app

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

COPY --from=python-builder /opt/python /opt/python
COPY --from=api-thin-environment /opt/venv /opt/venv
COPY --from=heavy-dependencies /layers/cuda-core/ /opt/venv/lib/python3.11/site-packages/
COPY --from=heavy-dependencies /layers/cuda-math/ /opt/venv/lib/python3.11/site-packages/
COPY --from=heavy-dependencies /layers/cuda-solver/ /opt/venv/lib/python3.11/site-packages/
COPY --from=heavy-dependencies /layers/torch/ /opt/venv/lib/python3.11/site-packages/

RUN uv pip check \
    && python -c "import importlib.util; import torch; import monkeyocr.infrastructure.pipeline.backends; assert importlib.util.find_spec('vllm') is None"

RUN groupadd --gid 10001 monkeyocr \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin monkeyocr \
    && install -d -o 10001 -g 10001 /var/lib/monkeyocr/results

USER 10001:10001

EXPOSE 8000

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["monkeyocr-api"]

FROM ${CUDA_DEVEL_IMAGE} AS vllm

ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG NO_PROXY=localhost,127.0.0.1,api,vllm

ENV PATH=/opt/venv/bin:/opt/python/bin:${PATH} \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /app

COPY --from=uv /uv /uvx /usr/local/bin/

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    apt-get update \
    && apt-get install --no-install-recommends -y \
        ca-certificates \
        curl \
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
        libnuma1 \
        libsm6 \
        libtcmalloc-minimal4 \
        libxext6 \
        tini \
    && update-ca-certificates

COPY --from=python-builder /opt/python /opt/python
COPY --from=vllm-thin-environment /opt/venv /opt/venv
COPY --from=heavy-dependencies /layers/cuda-core/ /opt/venv/lib/python3.11/site-packages/
COPY --from=heavy-dependencies /layers/cuda-math/ /opt/venv/lib/python3.11/site-packages/
COPY --from=heavy-dependencies /layers/cuda-solver/ /opt/venv/lib/python3.11/site-packages/
COPY --from=heavy-dependencies /layers/torch/ /opt/venv/lib/python3.11/site-packages/

RUN uv pip check \
    && python -c "import torch; import vllm; import monkeyocr.infrastructure.modeling.monkeyocr_vllm"

RUN groupadd --gid 10001 monkeyocr \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin monkeyocr \
    && install -d -o 10001 -g 10001 /var/lib/monkeyocr/results

USER 10001:10001

EXPOSE 8888

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["monkeyocr-vllm"]
