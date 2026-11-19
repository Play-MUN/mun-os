# The environment of tests/ci/check.sh, for running it as pull requests do:
#
#   docker build -f services/mun-shell/tests/ci/Dockerfile -t mun-shell-check .
#   docker run --rm -v "$PWD":/src -w /src mun-shell-check sh services/mun-shell/tests/ci/check.sh
#
# Debian 13 pinned by digest (the multi-platform index), then the image's own
# dated snapshot for every package (tests/ci/setup.sh).
FROM debian:trixie@sha256:9c6e381421d0f48d1b8133e573f33c185ee16cbf505eda9c4a8a4791b25ea8a6
WORKDIR /setup
COPY os/inputs.json os/inputs.json
COPY services/mun-shell/deploy/build-packages services/mun-shell/deploy/build-packages
COPY services/mun-shell/tests/ci/setup.sh services/mun-shell/tests/ci/setup.sh
RUN sh services/mun-shell/tests/ci/setup.sh && rm -rf /var/lib/apt/lists/*
