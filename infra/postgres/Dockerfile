# syntax=docker/dockerfile:1
# Docker-mode PostgreSQL (spec §4.1, §16.1): PG 18 + pgvector + pg_trgm (contrib) + pg_textsearch BM25.
ARG BASE=pgvector/pgvector:0.8.7-pg18-trixie

FROM ${BASE} AS build
ARG PG_TEXTSEARCH_VERSION=1.5.1
ARG PG_TEXTSEARCH_SHA256=5b90a160c4aa9ab78c72ab41eb761146a601485bff5e271f05a44d3da7493d5b
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential curl ca-certificates postgresql-server-dev-18 \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /src
RUN curl -fsSL -o pgt.tar.gz "https://github.com/timescale/pg_textsearch/releases/download/v${PG_TEXTSEARCH_VERSION}/pg_textsearch-${PG_TEXTSEARCH_VERSION}.tar.gz" \
 && echo "${PG_TEXTSEARCH_SHA256}  pgt.tar.gz" | sha256sum -c - \
 && tar xzf pgt.tar.gz --strip-components=1 \
 && make -j"$(nproc)" \
 && make install DESTDIR=/out

FROM ${BASE}
COPY --from=build /out/ /
CMD ["postgres", "-c", "shared_preload_libraries=pg_textsearch"]
