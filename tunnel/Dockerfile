# Small, explicit extension of the official 2026.9.3 source. No credentials in build.
FROM golang:1.26.8 AS build
WORKDIR /src
ADD --checksum=sha256:7459141c5769755f20de6f7edf32d3223f643a225a7f080772cb18baa2a08d27 https://codeload.github.com/cloudflare/cloudflared/tar.gz/96d39adbc812dc7363834bda908970c1a2560a72 /tmp/source.tar.gz
RUN tar -xzf /tmp/source.tar.gz --strip-components=1
COPY session-duration.patch .
RUN git apply --check session-duration.patch && git apply session-duration.patch
COPY session_policy.go session_policy_test.go quicktunnelauth/
RUN test -z "$(gofmt -l quicktunnelauth/session.go quicktunnelauth/session_policy*.go)"
RUN --mount=type=cache,target=/root/.cache/go-build --mount=type=cache,target=/go/pkg/mod \
    go test -mod=readonly -race ./quicktunnelauth
RUN --mount=type=cache,target=/root/.cache/go-build --mount=type=cache,target=/go/pkg/mod \
    CGO_ENABLED=0 go build -mod=readonly -trimpath \
    -ldflags='-s -w -X main.Version=2026.9.3-busqi.1 -X github.com/cloudflare/cloudflared/metrics.Runtime=virtual' \
    -o /cloudflared ./cmd/cloudflared

FROM cloudflare/cloudflared:2026.9.3
COPY --from=build /cloudflared /usr/local/bin/cloudflared
COPY --from=build /src/LICENSE /usr/share/licenses/cloudflared/LICENSE
USER 65532:65532
