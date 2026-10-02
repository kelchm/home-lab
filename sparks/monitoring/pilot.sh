#!/usr/bin/env bash
# Foreground evaluation collector. Ctrl-C/SIGTERM stops both children.
set -euo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PILOT_STATE_DIR="${PILOT_STATE_DIR:-${XDG_CACHE_HOME:-${HOME}/.cache}/home-lab/spark-monitoring-pilot}"
: "${VMAGENT_BIN:?Set VMAGENT_BIN to a verified vmagent binary (see README.md)}"
PILOT_KUBECONFIG="${PILOT_KUBECONFIG:-${KUBECONFIG:-}}"
: "${PILOT_KUBECONFIG:?Set PILOT_KUBECONFIG to the cluster kubeconfig}"

[[ -x "${VMAGENT_BIN}" && -f "${PILOT_KUBECONFIG}" ]] || {
    echo "VMAGENT_BIN must be executable and PILOT_KUBECONFIG must exist." >&2
    exit 1
}
"${VMAGENT_BIN}" -promscrape.config="${SCRIPT_DIR}/scrape.yaml" -promscrape.config.dryRun

mkdir -p "${PILOT_STATE_DIR}"
mkdir "${PILOT_STATE_DIR}/run.lock" || {
    echo "Pilot already running, or run.lock needs inspection: ${PILOT_STATE_DIR}/run.lock" >&2
    exit 1
}
port_forward_pid=""
collector_pid=""
# Invoked by EXIT; resetting traps inside the handler confuses ShellCheck.
# shellcheck disable=SC2329
cleanup() {
    trap - EXIT
    trap '' INT TERM
    # Keep the tunnel alive until vmagent has flushed/persisted its queue.
    [[ -z "${collector_pid}" ]] || kill "${collector_pid}" 2>/dev/null || true
    [[ -z "${collector_pid}" ]] || wait "${collector_pid}" 2>/dev/null || true
    [[ -z "${port_forward_pid}" ]] || kill "${port_forward_pid}" 2>/dev/null || true
    [[ -z "${port_forward_pid}" ]] || wait "${port_forward_pid}" 2>/dev/null || true
    rm -f "${PILOT_STATE_DIR}/run.lock/pid"
    rmdir "${PILOT_STATE_DIR}/run.lock"
}
trap 'cleanup' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
printf '%s\n' "$$" > "${PILOT_STATE_DIR}/run.lock/pid"

kubectl --kubeconfig "${PILOT_KUBECONFIG}" -n observability port-forward \
    --address 127.0.0.1 svc/vmsingle-victoria-metrics-k8s-stack 18428:8428 \
    > "${PILOT_STATE_DIR}/port-forward.log" 2>&1 &
port_forward_pid=$!

ready=false
for _ in {1..30}; do
    if ! kill -0 "${port_forward_pid}" 2>/dev/null; then
        cat "${PILOT_STATE_DIR}/port-forward.log" >&2
        exit 1
    fi
    if grep -q '^Forwarding from 127.0.0.1:18428' "${PILOT_STATE_DIR}/port-forward.log" && \
        curl --silent --fail --max-time 1 http://127.0.0.1:18428/health >/dev/null; then
        ready=true
        break
    fi
    sleep 1
done
if [[ "${ready}" != true ]]; then
    echo "VictoriaMetrics tunnel did not become ready within 30s." >&2
    exit 1
fi

"${VMAGENT_BIN}" \
    -promscrape.config="${SCRIPT_DIR}/scrape.yaml" \
    -httpListenAddr=127.0.0.1:18429 \
    -remoteWrite.url=http://127.0.0.1:18428/api/v1/write \
    -remoteWrite.tmpDataPath="${PILOT_STATE_DIR}/queue" \
    -remoteWrite.maxDiskUsagePerURL=600MB \
    -remoteWrite.queues=1 \
    -memory.allowedBytes=64MiB \
    > "${PILOT_STATE_DIR}/vmagent.log" 2>&1 &
collector_pid=$!
echo "Spark pilot started. Target status: http://127.0.0.1:18429/targets"
echo "Logs and shutdown PID: ${PILOT_STATE_DIR}"

# A dropped tunnel ends the pilot explicitly; do not leave a collector that
# appears healthy locally while indefinitely buffering without ingestion.
while kill -0 "${port_forward_pid}" 2>/dev/null && kill -0 "${collector_pid}" 2>/dev/null; do
    sleep 2
done
echo "Pilot child exited; stopping collection. Inspect logs before restarting." >&2
exit 1
