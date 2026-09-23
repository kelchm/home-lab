FROM golang:1.26.6-alpine@sha256:3889b425f035be855a72fb4755265311293b6d414521f0a519d819df32222d83 AS build
RUN apk add --no-cache patch
WORKDIR /src
ADD https://codeload.github.com/VictoriaMetrics/VictoriaLogs/tar.gz/ff8635e43c1dce4fe1d8b4dc5c57bdf3b62db248 /source.tar.gz
RUN echo '78a00e7d564f4e322210fa34292bded302dd682ebaf58b55ff2bf73b0025e327  /source.tar.gz' | sha256sum -c - && tar xzf /source.tar.gz --strip-components=1
COPY metadata.patch /metadata.patch
RUN patch -p1 < /metadata.patch
COPY homelab_test.go app/vlagent/kubernetescollector/homelab_test.go
RUN CGO_ENABLED=0 go test ./app/vlagent/kubernetescollector ./app/vlagent/tail ./app/vlagent/remotewrite
RUN CGO_ENABLED=0 go build -trimpath -ldflags='-s -w' -o /vlagent-prod ./app/vlagent
FROM victoriametrics/vlagent:heads-master-0-gff8635e43@sha256:7492cfbb1468432ca9db753bf9c3e4478b298f72945adaa902254627e9e3dba9
COPY --from=build /vlagent-prod /vlagent-prod
