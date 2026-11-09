# syntax=docker/dockerfile:1
# The Researchly app (spec §4.1 Docker mode). Build context: backend/; named context `frontend`: frontend/.
FROM node:24.22.2-trixie-slim AS web
RUN npm install --global pnpm@12.15.0
WORKDIR /repo/frontend
COPY --from=frontend package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY --from=frontend . .
# vite's outDir is ../backend/src/researchly/web
RUN pnpm build

FROM python:3.12-slim-trixie
COPY --from=ghcr.io/astral-sh/uv:0.12.10 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    LITELLM_LOCAL_MODEL_COST_MAP=True
WORKDIR /src
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY . .
COPY --from=web /repo/backend/src/researchly/web ./src/researchly/web
RUN uv sync --locked --no-dev --no-editable \
 && useradd --system --uid 10001 researchly
USER researchly
EXPOSE 8000
CMD ["uvicorn", "--factory", "researchly.app:create_app", "--loop", "asyncio:SelectorEventLoop", "--host", "0.0.0.0", "--port", "8000"]
