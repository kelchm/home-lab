#!/bin/sh
set -eu
mode=$1
arch=$2
version=$(cat /work/build-info/version)
if [ "$mode" = control ]; then
  commit=$(cat /work/build-info/stock-commit)
  branch=$(cat /work/build-info/stock-branch)
  stamp=$(cat /work/build-info/stock-buildstamp)
elif [ "$mode" = custom ]; then
  version="${version}+homelab.1"
  commit=$(cat /work/build-info/commit)
  branch=homelab-aurora
  stamp=$(cat /work/build-info/epoch)
else
  echo 'Expected control or custom build' >&2
  exit 1
fi
GOOS=linux GOARCH="$arch" go build -buildvcs=false -trimpath \
  -ldflags "-X main.version=${version} -X main.commit=${commit} -X main.buildBranch=${branch} -X main.buildstamp=${stamp}" \
  -o /work/grafana-server ./pkg/cmd/grafana
printf '%s (commit: %s, branch: %s)\n' "$version" "$commit" "$branch" > /work/grafana-version
