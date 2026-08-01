#!/usr/bin/env bash

set -euo pipefail

kubectl_bin="${KUBECTL_BIN:-kubectl}"
kubectl_args=()

if [[ -n "${KUBECONFIG_PATH:-}" ]]; then
  kubectl_args+=(--kubeconfig="${KUBECONFIG_PATH}")
fi

if [[ -n "${KUBE_API_SERVER:-}" ]]; then
  kubectl_args+=(--server="${KUBE_API_SERVER}")
fi

if [[ -n "${KUBE_TLS_SERVER_NAME:-}" ]]; then
  kubectl_args+=(--tls-server-name="${KUBE_TLS_SERVER_NAME}")
fi

for command in "${kubectl_bin}" jq; do
  command -v "${command}" >/dev/null || {
    echo "required command not found: ${command}" >&2
    exit 1
  }
done

exceptions='["kube-system","longhorn-system","media","network-perf","observability","tailscale"]'
controllers_file="$(mktemp)"
trap 'rm -f "${controllers_file}"' EXIT

echo "Auditing existing Pods in baseline-enforced namespaces..."
while IFS= read -r namespace; do
  "${kubectl_bin}" "${kubectl_args[@]}" label namespace "${namespace}" \
    pod-security.kubernetes.io/enforce=baseline \
    --overwrite --dry-run=server >/dev/null
done < <(
  "${kubectl_bin}" "${kubectl_args[@]}" get namespaces -o json | jq -r \
    --argjson exceptions "${exceptions}" \
    '.items[] | select(.metadata.name as $namespace | ($exceptions | index($namespace)) == null) | .metadata.name'
)

echo "Fetching final workload templates..."
"${kubectl_bin}" "${kubectl_args[@]}" get \
  deployments.apps,statefulsets.apps,daemonsets.apps,jobs.batch,cronjobs.batch,replicationcontrollers \
  --all-namespaces -o json >"${controllers_file}"

checked=0
while IFS= read -r check; do
  source="$(jq -r '.source' <<<"${check}")"
  namespace="$(jq -r '.namespace' <<<"${check}")"

  if ! jq -c '.pod' <<<"${check}" | \
    "${kubectl_bin}" "${kubectl_args[@]}" create --dry-run=server -f - >/dev/null; then
    echo "baseline preflight rejected ${namespace}/${source}" >&2
    exit 1
  fi

  ((checked += 1))
done < <(
  jq -c --argjson exceptions "${exceptions}" '
    .items[]
    | . as $controller
    | select(.metadata.namespace as $namespace | ($exceptions | index($namespace)) == null)
    | (if .kind == "CronJob" then .spec.jobTemplate.spec.template else .spec.template end) as $template
    | select($template.spec != null)
    | {
        source: ($controller.kind + "/" + $controller.metadata.name),
        namespace: $controller.metadata.namespace,
        pod: {
          apiVersion: "v1",
          kind: "Pod",
          metadata: {
            name: "psa-template-preflight",
            namespace: $controller.metadata.namespace,
            labels: ($template.metadata.labels // {}),
            annotations: ($template.metadata.annotations // {})
          },
          spec: (
            $template.spec
            + if $controller.kind == "StatefulSet" then {
                volumes: (
                  ($template.spec.volumes // [])
                  + (($controller.spec.volumeClaimTemplates // []) | map({
                      name: .metadata.name,
                      persistentVolumeClaim: {claimName: "psa-template-preflight"}
                    }))
                )
              } else {} end
          )
        }
      }
  ' "${controllers_file}"
)

echo "Pod Security baseline preflight passed for ${checked} workload templates."
