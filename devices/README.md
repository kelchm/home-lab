# Devices

Configuration and supporting tools for physical appliances that are managed
outside Kubernetes.

Unlike resources under `kubernetes/`, files in this directory are not
reconciled by Flux. Changes must be applied to each device manually, runtime
state may drift from the repository, and firmware upgrades may require the
configuration to be reapplied.

Each device directory documents its deployment and recovery procedure.
