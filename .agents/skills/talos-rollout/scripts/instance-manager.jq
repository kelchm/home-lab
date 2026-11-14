# Reasons a Longhorn instance-manager pod fails the rollout gates; empty when
# it passes. Used by failing-instance-managers.sh.
#
# Multus records the pod's attachments in its network-status annotation when
# it sets up the pod sandbox. A sandbox recreated without Multus, as after a
# reboot while the Multus CNI conf is missing, leaves the previous sandbox's
# annotation on the same pod, claiming an lhnet1 the pod no longer has. Its
# default network then usually names the old pod IP, so require it to match
# status.podIP. A reused IP defeats this; failing-instance-managers.sh also
# checks lhnet1 inside the pod.
def instance_manager_problems:
  (.status.containerStatuses // []) as $containers
  | ((.metadata.annotations["k8s.v1.cni.cncf.io/network-status"] // "[]")
      | fromjson? // []
      | if type == "array" then . else [] end) as $networks
  | (.status.podIP // "") as $pod_ip
  | [
      (if .status.phase != "Running" then "phase \(.status.phase // "unknown")" else empty end),
      (if ($containers | length) == 0 or (all($containers[]; .ready == true) | not)
        then "not Ready" else empty end),
      (if [$networks[]
            | select(.name == "longhorn-system/storage-network"
                and .interface == "lhnet1"
                and any(.ips[]?; startswith("10.32.25.")))] | length == 0
        then "no storage-network on lhnet1" else empty end),
      (if $pod_ip == "" or ([$networks[] | select(.default == true) | .ips[]?] | any(. == $pod_ip) | not)
        then "network-status is not for this pod (default-network IP is not status.podIP \($pod_ip))"
        else empty end)
    ];
