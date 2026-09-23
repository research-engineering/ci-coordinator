FROM mcr.microsoft.com/devcontainers/base:2-ubuntu-24.04@sha256:d94c97dd9cacf183d0a6fd12a8e87b526e9e928307674ae9c94139139c0c6eae

ARG TARGETARCH
ARG MISE_VERSION=2026.9.11
RUN case "${TARGETARCH}" in \
      amd64) mise_arch=x64; mise_sha=6e39ee1ffa926a1f64814efbf1d26c3f617255e2a6ba6152e3c8a5135fa5a35f ;; \
      arm64) mise_arch=arm64; mise_sha=80ad6e9589d3c4483fd67a4a97f07e28330964dde64ca8cad15d5cb56134a54d ;; \
      *) exit 2 ;; \
    esac \
    && curl --fail --location --silent --show-error \
      "https://github.com/jdx/mise/releases/download/v${MISE_VERSION}/mise-v${MISE_VERSION}-linux-${mise_arch}" \
      --output /usr/local/bin/mise \
    && echo "${mise_sha}  /usr/local/bin/mise" | sha256sum --check --strict \
    && chmod 0755 /usr/local/bin/mise
