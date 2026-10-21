#!/usr/bin/env bash
# Exercise version dispatch without production credentials, mise, or node RPCs.
set -Eeuo pipefail

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly TEST_DIR="$(mktemp -d)"
trap 'rm -rf "$TEST_DIR"' EXIT
mkdir "$TEST_DIR/bin"
printf 'fixture\n' > "$TEST_DIR/kubeconfig"
printf 'fixture\n' > "$TEST_DIR/talosconfig"
export KUBECONFIG="$TEST_DIR/kubeconfig" TALOSCONFIG="$TEST_DIR/talosconfig"
export CLIENT_TEST_LOG="$TEST_DIR/command.log"

cat > "$TEST_DIR/bin/kubectl" <<'STUB'
#!/usr/bin/env bash
set -eu
[[ "$1" == --kubeconfig && "$2" == "$KUBECONFIG" ]] || exit 1
[[ "${CLIENT_TEST_LOOKUP_FAIL:-}" != 1 ]] || exit 1
case "$5" in
    k8s-prod-1) printf '%s' 'Talos (v1.13.11)' ;;
    k8s-prod-2) printf '%s' 'Talos (v1.14.4)' ;;
    k8s-prod-3) printf '%s' 'unrecognized OS' ;;
    *) exit 1 ;;
esac
STUB
cat > "$TEST_DIR/bin/mise" <<'STUB'
#!/usr/bin/env bash
set -eu
printf '%s\n' "$@" > "$CLIENT_TEST_LOG"
STUB
chmod +x "$TEST_DIR/bin/"*
export PATH="$TEST_DIR/bin:$PATH"

"$SCRIPT_DIR/with-node-client.sh" 10.32.30.11 talosctl version
grep -Fxq 'aqua:siderolabs/talos@1.13.11' "$CLIENT_TEST_LOG"
"$SCRIPT_DIR/with-node-client.sh" k8s-prod-2 talosctl etcd members
grep -Fxq 'aqua:siderolabs/talos@1.14.4' "$CLIENT_TEST_LOG"
grep -Fxq 'members' "$CLIENT_TEST_LOG"
echo 'ok: mixed nodes select their respective clients and preserve arguments'

expect_no_dispatch() {
    rm -f "$CLIENT_TEST_LOG"
    if "$SCRIPT_DIR/with-node-client.sh" "$@" > "$TEST_DIR/output" 2>&1; then
        echo 'unexpected successful dispatch' >&2
        exit 1
    fi
    [[ ! -e "$CLIENT_TEST_LOG" ]] || { echo 'command ran after failed selection' >&2; exit 1; }
}

expect_no_dispatch 10.32.30.13 talosctl upgrade
expect_no_dispatch 10.32.30.99 talosctl upgrade
CLIENT_TEST_LOOKUP_FAIL=1 expect_no_dispatch 10.32.30.11 talosctl upgrade
TALOSCONFIG="$TEST_DIR/missing" expect_no_dispatch 10.32.30.11 talosctl upgrade
echo 'ok: unknown OS, unknown node, failed lookup and missing credentials stop execution'
