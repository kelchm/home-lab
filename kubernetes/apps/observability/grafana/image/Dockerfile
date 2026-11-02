ARG GRAFANA_IMAGE=grafana/grafana:13.3.0-distroless@sha256:540b8afb775bf34aefce417e4329039daa5d44b6f2fbda6881397b1b247ca7df
ARG NODE_IMAGE=node:24.22.1-alpine@sha256:06d7adf3e0ef61a13f1d28682726366f97a15aa1888c934d3ecec86c33918b53
ARG GO_IMAGE=golang:1.27.3-alpine@sha256:d200d2dd76b03a99b52651fd1325aff7dcd35422c35c01bac56571b0507f3deb

FROM ${GRAFANA_IMAGE} AS stock

FROM --platform=${BUILDPLATFORM} ${NODE_IMAGE} AS source
ARG GRAFANA_IMAGE
WORKDIR /work
COPY source-lock.json ./
COPY tools tools
COPY --from=stock /.grafana-version /work/stock-version
RUN node tools/prepare.mjs /work /work/grafana "${GRAFANA_IMAGE}"

FROM source AS dependencies
WORKDIR /work/grafana
ENV NX_DAEMON=false NX_NO_CLOUD=true NX_SKIP_NX_CACHE=true
ENV YARN_ENABLE_GLOBAL_CACHE=false YARN_CACHE_FOLDER=/cache/yarn
COPY --chmod=755 tools/yarn /usr/local/bin/yarn
RUN --mount=type=cache,target=/cache/yarn yarn install --immutable

FROM dependencies AS frontend-control
RUN yarn build
RUN --mount=type=bind,from=stock,source=/usr/share/grafana/public/build,target=/stock-build \
    node /work/tools/compare-tree.mjs /stock-build public/build /work/frontend-parity.json

FROM --platform=${BUILDPLATFORM} ${GO_IMAGE} AS backend-control
ARG TARGETARCH
WORKDIR /work/grafana
ENV CGO_ENABLED=0 GOTOOLCHAIN=local GOMAXPROCS=2 GOFLAGS=-p=2
COPY --from=source /work/grafana ./
COPY --from=source /work/build-info /work/build-info
COPY tools/build-backend.sh /work/build-backend.sh
# Serialize the expensive control builds on small builders, and require both parity gates.
COPY --from=frontend-control /work/frontend-parity.json /work/frontend-parity.json
RUN --mount=type=cache,target=/go/pkg/mod --mount=type=cache,target=/root/.cache/go-build \
    --mount=type=bind,from=stock,source=/usr/share/grafana/bin/grafana,target=/stock-grafana \
    sh /work/build-backend.sh control "${TARGETARCH}" && \
    cmp /work/grafana-server /stock-grafana && \
    printf '{"status":"identical","sha256":"%s"}\n' "$(sha256sum /work/grafana-server | cut -d' ' -f1)" > /work/backend-parity.json

FROM frontend-control AS frontend
COPY --from=backend-control /work/backend-parity.json /work/backend-parity.json
COPY theme.json /work/theme.json
RUN node /work/tools/register.mjs /work /work/grafana && node /work/tools/validate.mjs /work/grafana
RUN yarn build

FROM backend-control AS backend
ARG TARGETARCH
COPY --from=frontend /work/grafana/packages/grafana-data/src/themes/themeDefinitions/homelab_aurora.json ./packages/grafana-data/src/themes/themeDefinitions/homelab_aurora.json
RUN --mount=type=cache,target=/go/pkg/mod --mount=type=cache,target=/root/.cache/go-build \
    go generate ./pkg/services/preference && sh /work/build-backend.sh custom "${TARGETARCH}" && \
    printf '{"sha256":"%s"}\n' "$(sha256sum /work/grafana-server | cut -d' ' -f1)" > /work/backend-custom.json

FROM ${GRAFANA_IMAGE}
COPY --from=backend /work/grafana-server /usr/share/grafana/bin/grafana
COPY --from=backend /work/grafana-version /.grafana-version
COPY --from=frontend /work/grafana/public/build/ /usr/share/grafana/public/build/
COPY --from=source /work/source-manifest.json /usr/share/grafana/homelab-theme/source-manifest.json
COPY --from=backend /work/backend-parity.json /work/backend-custom.json /work/frontend-parity.json /usr/share/grafana/homelab-theme/
COPY --from=frontend /work/registration.json /usr/share/grafana/homelab-theme/registration.json
COPY source-lock.json theme.json Dockerfile NOTICE.md /usr/share/grafana/homelab-theme/
COPY tools /usr/share/grafana/homelab-theme/tools/
# Keep activation in the artifact, so reverting to stock cannot retain an unknown default ID.
ENV GF_USERS_DEFAULT_THEME=homelab_aurora
ENV GF_FEATURE_TOGGLES_grafana.visualDesignRefresh=true
LABEL org.opencontainers.image.source="https://github.com/kelchm/home-lab"
LABEL org.opencontainers.image.description="Grafana with a registered Homelab Aurora theme and exact upstream control-build gates"
LABEL org.opencontainers.image.licenses="AGPL-3.0-only"
