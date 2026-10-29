#!/usr/bin/env bash
# Destructive host sysctl is allowed ONLY on the disposable hosted Linux runner.
set -euo pipefail
[[ ${GITHUB_ACTIONS:-} == true && ${RUNNER_ENVIRONMENT:-} == github-hosted ]]
[[ $(uname -s) == Linux && $(uname -m) == x86_64 ]]
image=${1:?exact image reference or local PR image ID}
report=${2:?qualification report directory}
mkdir -p "$report"
report=$(realpath "$report")
guest=$(realpath proxmox/guests/hermes-1)
scratch=$(mktemp -d)
name="hermes-caddy-qualify-${GITHUB_RUN_ID:?}-${GITHUB_RUN_ATTEMPT:?}"
fixture_pid=
original_threshold=$(sysctl -n net.ipv4.ip_unprivileged_port_start)
cleanup() {
    docker logs "$name" > "$report/caddy.log" 2>&1 || true
    docker logs "$name-init" > "$report/init.log" 2>&1 || true
    docker rm -f "$name" "$name-init" >/dev/null 2>&1 || true
    if [[ -n $fixture_pid ]]; then
        kill "$fixture_pid" 2>/dev/null || true
        wait "$fixture_pid" 2>/dev/null || true
    fi
    sudo sysctl -w "net.ipv4.ip_unprivileged_port_start=$original_threshold" >/dev/null
    sudo rm -rf "$scratch"
}
trap cleanup EXIT
sudo sysctl -w net.ipv4.ip_unprivileged_port_start=1024
[[ $(sysctl -n net.ipv4.ip_unprivileged_port_start) == 1024 ]]
sysctl net.ipv4.ip_unprivileged_port_start > "$report/host-sysctl.txt"

python3 scripts/ci/hermes-caddy-inputs.py expected > "$report/expected.txt"
builder=$(sed -n 's/^builder=//p' "$report/expected.txt")
runtime=$(sed -n 's/^runtime=//p' "$report/expected.txt")
for base in "$builder" "$runtime"; do docker pull --platform linux/amd64 "$base"; done
docker image inspect "$builder" > "$report/builder.json"
docker image inspect "$runtime" > "$report/runtime.json"
docker run --rm --network none "$builder" go version > "$report/go-version.txt"
docker run --rm --network none "$runtime" caddy version > "$report/runtime-version.txt"
docker run --rm --network none "$image" caddy version > "$report/version.txt"
docker run --rm --network none "$image" caddy build-info > "$report/build-info.txt"
docker run --rm --network none "$image" caddy list-modules --skip-standard > "$report/modules.txt"
python3 scripts/ci/hermes-caddy-inputs.py verify "$report" > "$report/inventory.json"
cat "$report/inventory.json"
[[ $(docker image inspect -f '{{.Os}}/{{.Architecture}} {{.Config.User}}' "$image") == 'linux/amd64 1000:1000' ]]

chmod 755 "$scratch"
mkdir -p "$scratch/data" "$scratch/config"
sudo chown 1000:1000 "$scratch/data" "$scratch/config"
openssl req -x509 -newkey rsa:2048 -nodes -days 2 -subj /CN=Caddy-CI-CA \
    -keyout "$scratch/ca.key" -out "$scratch/ca.pem" 2>/dev/null
openssl req -newkey rsa:2048 -nodes -subj /CN=caddy.test \
    -keyout "$scratch/leaf.key" -out "$scratch/leaf.csr" 2>/dev/null
printf 'subjectAltName=DNS:caddy.test\nextendedKeyUsage=serverAuth\n' > "$scratch/extensions"
openssl x509 -req -days 1 -in "$scratch/leaf.csr" -CA "$scratch/ca.pem" -CAkey "$scratch/ca.key" \
    -CAcreateserial -extfile "$scratch/extensions" -out "$scratch/leaf.pem" 2>/dev/null
openssl req -x509 -newkey rsa:2048 -nodes -days 2 -subj /CN=Wrong-CI-CA \
    -keyout "$scratch/wrong.key" -out "$scratch/wrong.pem" 2>/dev/null
# Only a disposable fixture key; no real certificate, token or config is copied.
chmod 644 "$scratch/leaf.key"
cat > "$scratch/Caddyfile" <<'CADDY'
{
    admin off
    auto_https off
}
https://caddy.test {
    bind 127.0.0.1
    tls /fixture/leaf.pem /fixture/leaf.key
    reverse_proxy 127.0.0.1:9119
}
CADDY
# ShellCheck mistakes the intentional comma-separated tmpfs options for an array.
# shellcheck disable=SC2054
contract=(--user 1000:1000 --read-only --cap-drop ALL --cap-add NET_BIND_SERVICE
    --security-opt no-new-privileges:true --tmpfs /tmp:rw,noexec,nosuid,nodev,size=16m
    --mount "type=bind,src=$scratch/data,dst=/data"
    --mount "type=bind,src=$scratch/config,dst=/config")
# Provision the actual production config offline. Never emit adapted JSON or an
# interpolated token; the only credential here is a literal dummy.
docker run --rm --network none "${contract[@]}" \
    --mount "type=bind,src=$guest/Caddyfile,dst=/etc/caddy/Caddyfile,readonly" \
    --env CLOUDFLARE_API_TOKEN=0000000000000000000000000000000000000000 \
    "$image" caddy adapt --validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null

python3 "$guest/caddy/fixture.py" serve > "$report/upstream.log" 2>&1 &
fixture_pid=$!
for _ in {1..30}; do
    if curl --noproxy '*' --fail --silent http://127.0.0.1:9119/api/status >/dev/null; then break; fi
    sleep 1
done
kill -0 "$fixture_pid"
common=(--network host "${contract[@]}"
    --mount "type=bind,src=$scratch,dst=/fixture,readonly"
    --add-host caddy.test:127.0.0.1 --env CURL_CA_BUNDLE=/fixture/ca.pem --env NO_PROXY=\*)
docker run -d --name "$name" "${common[@]}" \
    --health-cmd 'curl --fail --silent --show-error --output /dev/null https://caddy.test/api/status' \
    --health-interval 2s --health-timeout 5s --health-retries 3 \
    "$image" caddy run --config /fixture/Caddyfile --adapter caddyfile
for _ in {1..30}; do
    if curl --noproxy '*' --resolve caddy.test:443:127.0.0.1 --cacert "$scratch/ca.pem" \
        --fail --silent https://caddy.test/api/status > "$report/status.json"; then break; fi
    sleep 1
done
python3 -c 'import json,sys; assert json.load(open(sys.argv[1])) == {"fixture":"hermes-caddy", "path":"/api/status"}' "$report/status.json"
if curl --noproxy '*' --resolve caddy.test:443:127.0.0.1 --cacert "$scratch/wrong.pem" \
    --silent --output /dev/null https://caddy.test/api/status; then
    echo 'Untrusted TLS unexpectedly succeeded' >&2; exit 1
else
    [[ $? == 60 ]]
fi
python3 "$guest/caddy/fixture.py" check --ca "$scratch/ca.pem"
[[ $(docker exec "$name" cat /proc/1/comm) == caddy ]]
docker exec "$name" cat /proc/1/status > "$report/pid1-status.txt"
python3 - "$report/pid1-status.txt" <<'PY'
import sys
status = dict(line.split(':', 1) for line in open(sys.argv[1]) if ':' in line)
assert status['Uid'].split() == ['1000'] * 4
assert status['Gid'].split() == ['1000'] * 4
assert int(status['CapEff'].strip(), 16) == 1 << 10
assert status['NoNewPrivs'].strip() == '1'
PY
docker exec "$name" curl --fail --silent --show-error --output /dev/null https://caddy.test/api/status
for _ in {1..15}; do
    [[ $(docker inspect -f '{{.State.Health.Status}}' "$name") == healthy ]] && break
    sleep 1
done
[[ $(docker inspect -f '{{.State.Health.Status}}' "$name") == healthy ]]
docker stop -t 5 "$name" >/dev/null
# Same image, same real port, host threshold and contract, with only --init
# added. A fake 101 or a high-port bind would miss the historical regression.
docker run -d --name "$name-init" --init "${common[@]}" \
    "$image" caddy run --config /fixture/Caddyfile --adapter caddyfile
for _ in {1..15}; do
    [[ $(docker inspect -f '{{.State.Running}}' "$name-init") == false ]] && break
    sleep 1
done
[[ $(docker inspect -f '{{.State.Running}} {{.State.ExitCode}}' "$name-init") =~ ^false\ [1-9] ]]
docker logs "$name-init" > "$report/init.log" 2>&1
grep -E 'bind.*permission denied|permission denied.*bind' "$report/init.log" | grep ':443'
echo 'Native amd64 qualification passed, including the --init privileged-bind negative control.'
