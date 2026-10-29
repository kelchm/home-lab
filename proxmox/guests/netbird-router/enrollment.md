# Router enrollment

Prepared procedure only: no helper has been installed and no new enrollment is authorized. Use this for an approved rebuild/re-enrollment of 103 or the separately reviewed second router. It requires a reachable Main administrator session, a local recovery path and a NetBird administrator. Never enroll a cloned enrolled identity; fence the old instance before replacing it.

## Helper and logging boundary

[`netbird-router-enroll.sh`](netbird-router-enroll.sh) is credential-free. For a 103 rebuild, separately stage this reviewed file as `/usr/local/sbin/netbird-router-enroll`, root:root `0755`, and compare its SHA-256 with the reviewed checkout before invoking it. Refuse a symlink or nonmatching existing destination; do not overwrite a live helper blindly. This is separate from the frozen 103 vendor data. Verify GNU `timeout`, `mktemp`, the NetBird CLI and support for `--setup-key-file` before creating any key. The second router's vendor data embeds the same helper.

The helper bounds stdin delivery plus `netbird up` to 90 seconds, followed by a 5-second forced-termination allowance. It accepts no arguments, creates a unique `0600` file under `/run` with `umask 077`, and passes only that path to `netbird up --setup-key-file`, with client routes and DNS disabled. Exit and catchable signal traps remove the file. SIGKILL, kernel failure and loss of the guest can defeat cleanup; do not claim that deletion is guaranteed. A short-lived, one-use key and verified end-of-window revocation regardless of success are the backstop. The private delivery file and its cleanup mechanism remain outside Git/cloud-init.

The old documented `--setup-key` argument could persist in guest syslog/journal, node sudo command logs and both process lists. The recorded historical 103 key is one-off, used once and expired; private historical log contents are unknown. No historical disclosure has been established, and no log retrieval, scrubbing or rotation is proposed.

Source inspection establishes a narrower boundary: [QEMU v10.0.0 guest-exec](https://github.com/qemu/qemu/blob/v10.0.0/qga/commands.c) logs argv, while stdin arrives as `input-data` separately. [NetBird v0.79.0 root flags](https://github.com/netbirdio/netbird/blob/v0.79.0/client/cmd/root.go) read/trim the setup-key file; its [daemon Login path](https://github.com/netbirdio/netbird/blob/v0.79.0/client/server/server.go) does not log the request/key and its [profile config](https://github.com/netbirdio/netbird/blob/v0.79.0/client/internal/profilemanager/config.go) has no setup-key field. These inspected paths do not establish secrecy in every upstream log or every client version.

Before future enrollment, freshly inspect safe audit configuration on the selected node and guest: sudo `log_input`/`log_output` and I/O plugins, auditd/exec tracing, debug/auth logging, plus the delivery mechanism and Cloud audit behavior. Inspect configuration, not secret-bearing logs. The installed guest-agent version and any changed NetBird client source also need comparison. Node/guest sudo I/O capture, audit/debug auth and Cloud audit contents are unverified. If stdin capture cannot be excluded, the owner uses a private console with an assessed logging boundary and the same stdin-only helper. Do not create or read a key until that gate passes; do not promise secrecy for uninspected logging upstream of the helper.

## Stage the helper for a rebuilt 103

Only in the approved rebuild window, transfer the credential-free helper from the reviewed workstation checkout to a unique node-1 directory:

```bash
transfer=$(ssh kelchm@10.32.20.21 'umask 077; mktemp -d /tmp/netbird-helper.XXXXXX')
scp proxmox/guests/netbird-router/netbird-router-enroll.sh "kelchm@10.32.20.21:$transfer/"
printf 'Helper transfer directory: %s\n' "$transfer"
```

In the node-1 session, substitute that exact directory below. Require the intended rebuilt 103, a responding guest agent and no enrollment in progress. This passes only the reviewed script on stdin; no setup key is involved. The guest stages it under `/run`, verifies the pinned hash and publishes a new destination exclusively. An existing regular file must match exactly, including owner/mode; a symlink or different file stops the procedure.

```bash
transfer='/tmp/netbird-helper.<this-run>'
sudo -n qm guest exec 103 --pass-stdin 1 --timeout 30 -- \
  timeout --kill-after=2s 20s /bin/sh -ceu '
    test "$(id -u)" -eq 0
    umask 077
    staged=$(mktemp /run/netbird-helper-stage.XXXXXX)
    trap "rm -f -- \"$staged\"" EXIT
    cat > "$staged"
    printf "9ca51dcf2e56f739f5afeb948c106e0116366933ab1b28c4ee9a68223503f433  %s\n" "$staged" | sha256sum --check --status
    chmod 0755 "$staged"
    destination=/usr/local/sbin/netbird-router-enroll
    test ! -L "$destination"
    if test -e "$destination"; then
      test -f "$destination"
      cmp -- "$staged" "$destination"
    else
      ln -T -- "$staged" "$destination"
    fi
    test "$(stat -c "%U:%G %a" "$destination")" = "root:root 755"
    sha256sum "$destination"
  ' < "$transfer/netbird-router-enroll.sh"
```

Require the returned `exited: 1`, `exitcode: 0` and expected SHA-256. A PID-only response needs `qm guest exec-status 103 <pid>` within 30 seconds of the initial call; a missing/ambiguous result stops enrollment until the exact guest task and installed file are reconciled. The helper is never executed during staging. Remove only this run's node transfer file/directory after successful verification. The second router gets the identical helper from its reviewed vendor data and does not need this staging step.

## Approved-window sequence

1. Freshly read the current router group by stable ID, not its possibly renamed label; retain the existing router, Network, masquerade, metric and policy semantics. Check peer-approval settings and policies referring to `All` or the routers group before adding a peer. Use only the intended routers auto-group, never clients. Withdraw a previous same-instance peer only as part of the approved replacement; never remove the surviving router.
2. An administrator creates a non-ephemeral setup key, usage limit 1, shortest practical expiry. Record only key ID/name/expiry, not its value. Keep the delivery mechanism and private file outside Git, cloud-init, issue text and shell history; shell tracing is off. An agent requiring delivery must use the approved private file mechanism rather than ask for a value in chat.
3. Invoke the helper once, passing the key on stdin. Example for an approved 103 rebuild from Main (104 uses node `.22` and VMID `104`):

   ```sh
   ssh kelchm@10.32.20.21 \
     'sudo -n qm guest exec 103 --pass-stdin 1 --timeout 120 -- /usr/local/sbin/netbird-router-enroll' \
     < "$SETUP_KEY_DELIVERY_FILE"
   ```

4. Retain the returned PID and inspect `exited` and `exitcode` explicitly. If only a PID is returned, poll `sudo qm guest exec-status 103 <pid>` at most every 2 seconds for at most 30 seconds after the initial response; accept only `exited: 1` and `exitcode: 0`. A transport failure, missing PID, polling deadline, nonzero exit or lost session is failure/ambiguity, never permission to send the key again. The helper has its own execution deadline independent of the operator connection. If completion is ambiguous, retain the task/PID metadata, stop enrollment work and inspect via a safe management/console path; do not kill unrelated processes or assume the peer was not created.
5. Revoke the key at the end of this window on success, failure **or ambiguity**. Normally confirm the guest process has exited first; if that cannot be established by the bound, revoke immediately as containment and leave the outcome unresolved. Verify the key's revoked/expired state from metadata, clean up the private delivery file using its delivery mechanism and safely inspect for a leftover `/run/netbird-router-enroll.*` file only after the process exits. Revocation prevents another enrollment; it does not remove an already enrolled peer. Do not delete a peer just to revoke a key.
6. Verify both Management and Signal connected, the intended router group/peer approval and expiration settings, no clients-group membership, expected resource prefixes, disabled client routes/DNS, and the same enrolled peer still present after revocation. Use allowlisted metadata and the [read-only export](../../../scripts/netbird-export.sh), never dump the guest's private configuration. Router probes and away-client acceptance remain separate gates.

A future retry uses a new approved one-use key only after reconciling any partially enrolled peer. Never put a key in argv, a fixed `/run` path, an SSH command string or a cloud-init field.
