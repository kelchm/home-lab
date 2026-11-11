# GL.iNet KVM customizations

Configuration for the GL-RM1PE at `10.32.20.10`. This is manually applied
appliance state; Flux does not reconcile it.

Firmware `V1.10.1 release2` (`/etc/version`), build `rm10-1.10.0beta2-30-g265a7f2656-dirty` (`/etc/os-release`), kvmd 4.82, verified 2026-10-17. GL.iNet builds RM1PE firmware from its RM10 tree, so the build string names the RM10 while `/etc/version` and `/proc/gl-hw-info/model` say RM1PE.

## Web interfaces

The stock GL.iNet UI serves the console, firmware updates, and appliance management at `https://10.32.20.10/`. The PiKVM frontend is disabled. An optional [Hosts bar](#optional-hosts-bar) at `/hosts/` frames the stock console and adds downstream switching buttons; the normal root address remains the stock UI.

| Port | Interface |
|------|-----------|
| 443 | Stock GLKVM |
| 8888 | Disabled |

Port 80 redirects to HTTPS. The active nginx configuration is copied unchanged from `/rom/etc/kvmd/nginx-kvmd.conf`, supplied by the installed firmware, and uses the certificate in `/etc/kvmd/user/ssl`. Do not restore an old custom nginx file after a firmware update.

Restored 2026-10-20 by validating and copying the firmware's nginx configuration, then reloading nginx. The prior web configuration is backed up on the appliance at `/etc/kvmd/user/backups/nginx-kvmd.conf.before-stock-ui-20261020`. The USB compatibility fix, monitoring, and SSH helpers remain applied; switching web interfaces does not require rebuilding USB or restarting kvmd.

## Name and address

Applied 2026-11-11: the UniFi record answers `10.32.20.10` from `10.32.10.1`, `10.32.20.1` and `10.32.30.1`; `hostname` and `/etc/hostname` read `kvm-compute-rack` (set the way the GL UI does: write `/etc/hostname`, run `hostname`, restart `gl_mdns`); connman reports `Method=manual` with the address, gateway and nameserver above. The previous DHCP settings are kept at `/etc/kvmd/user/backups/connman-settings.before-static`. Persistence across an appliance reboot has not yet been observed.

The appliance is `kvm-compute-rack.home.kelch.io` at `10.32.20.10`, following the role-plus-rack pattern of `ups-compute-rack`. The name outlives the GL.iNet hardware; the `devices/glkvm/` directory keeps the vendor name because its contents are vendor-specific.

| Setting | Where it is set | Survives a firmware update |
|---|---|---|
| DNS record `kvm-compute-rack.home.kelch.io → 10.32.20.10` | UniFi local DNS | Not on the appliance |
| Hostname `kvm-compute-rack` | GL UI hostname setting, which writes `/etc/hostname` | Yes; the firmware's own pre-update backup keeps `/etc/hostname` |
| Static `10.32.20.10/24`, gateway and DNS `10.32.20.1` | GL UI network settings (connman) | Yes; `/var/lib/connman` links to `/etc/kvmd/user/connman` |

The address is static on the device so the recovery console does not depend on DHCP. UniFi keeps its fixed assignment for the MAC address as a guard against handing `10.32.20.10` to anything else. PiKVM's `server.host` in `/etc/kvmd/meta.yaml` is left unchanged because the stock UI does not display it.

On a firmware update the firmware itself backs up and restores `/etc/kvmd/user`, `/etc/kvmd/override.yaml`, `/etc/hostname`, `/etc/glinet`, `/etc/shadow`, Dropbear host keys, `/root/.ssh` and the timezone (`/etc/init.d/S23config`). Everything else under `/` can revert to the firmware's copy. That is why all appliance state added here lives in `/etc/kvmd/user`, and anything outside it is restored by a boot script.

## TLS

Applied 2026-11-11: the first order created the Lego account and issued a Let's Encrypt (`YE2`) certificate for `kvm-compute-rack.home.kelch.io`, valid until 2027-02-09. A workstation `curl` to the hostname returns HTTP 200 with verify result `0`, and the unauthenticated metrics scrape still returns 200. A scheduled renewal and the boot script's restore of the cron entry after a reboot have not yet been observed.

nginx serves a Let's Encrypt certificate for `kvm-compute-rack.home.kelch.io` from `/etc/kvmd/user/ssl/server.{crt,key}`, the files the firmware's `ssl.conf` reads. [Lego](https://go-acme.github.io/lego/) issues and renews it on the appliance with a Cloudflare DNS-01 challenge, the same way as [Athena](../../docs/runbooks/athena-acme.md). It does not use the cluster's cert-manager: the console must work while the cluster is down.

| Path on the appliance | Contents |
|---|---|
| `/etc/kvmd/user/acme/bin/lego` | Pinned Lego 5.6.0 for `linux/arm64` |
| `/etc/kvmd/user/acme/lego.yml` | [`acme/lego.yml`](acme/lego.yml), the only file that names this site's host; the scripts are generic |
| `/etc/kvmd/user/acme/renew.sh` | [`acme/renew.sh`](acme/renew.sh), run daily at 03:41 local time |
| `/etc/kvmd/user/acme/deploy.sh` | [`acme/deploy.sh`](acme/deploy.sh), Lego's hook: checks the new pair, installs it, validates nginx and reloads it, restoring the previous pair if nginx rejects it |
| `/etc/kvmd/user/acme/cloudflare-token` | The `kvm-acme` Cloudflare token, mode `0600`. Never in Git |
| `/etc/kvmd/user/acme/lego-state/` | Lego's account, key and certificates |
| `/etc/kvmd/user/acme/renewal.log` | Every run, appended; trimmed to the last 2,000 lines once it passes 5,000 |
| `/etc/kvmd/user/scripts/S50acme-renewal` | [`acme/S50acme-renewal`](acme/S50acme-renewal), run at boot by the firmware's `S99custom`; restores the cron entry in `/etc/crontabs/root` |
| `/etc/kvmd/user/backups/ssl-vendor/` | The firmware's self-signed pair, kept once before the first replacement |

The firmware starts two `crond` processes that read the same crontab, so every entry fires twice; `renew.sh` holds a lock and the second copy exits. Lego renews only when the certificate is due and keeps the same key. nginx's startup script regenerates a self-signed pair only if the files are missing or the key doesn't match, never because the certificate expired.

The firmware sends `Strict-Transport-Security` with a one-year `max-age` (`/etc/kvmd/nginx/ssl.conf`). Once a browser has seen the trusted certificate on the hostname, it will refuse to click through a broken one. If renewal ever fails and the certificate expires, use `https://10.32.20.10/`: HSTS does not apply to an IP address, so the browser allows the usual warning. Nothing yet alerts on an expiring certificate; adding the rack Pi's TLS probe for this host is tracked in [#419](https://github.com/kelchm/home-lab/issues/419).

The `kvm-acme` token has `Zone:Zone:Read` and `Zone:DNS:Edit` on `kelch.io` and is used only here, so it can be revoked without touching Athena or S3. Its 1Password item is `op://Project - home-lab/kvm-acme/credential`. Cloudflare cannot scope a token to one record, so a stolen token could edit any `kelch.io` record. Anyone who can read it already has root on the console that types into every host's BIOS.

### Install or update

Install the client from a repository checkout. This downloads and verifies Lego, installs the files above and the cron entry, and leaves the token, Lego's state and the served certificate unchanged:

```sh
devices/glkvm/acme/install.sh
```

Install the token on first setup or after rotating it:

```sh
op read 'op://Project - home-lab/kvm-acme/credential' |
    ssh root@10.32.20.10 'umask 077; cat > /etc/kvmd/user/acme/cloudflare-token'
```

Request the first certificate, or retry a failed run, by running the daily job by hand and reading its log:

```sh
ssh root@10.32.20.10 '/etc/kvmd/user/acme/renew.sh; status=$?; tail -n 30 /etc/kvmd/user/acme/renewal.log; exit $status'
```

Lego saves an issued certificate before running its hook. If the hook failed, a later run that finds the certificate not yet due does not retry it; fix the cause and run `/etc/kvmd/user/acme/deploy.sh kvm-compute-rack.home.kelch.io` by hand.

Check what the listener serves from a workstation, bypassing DNS but verifying the name:

```sh
curl -sS -o /dev/null -w '%{http_code} %{ssl_verify_result}\n' \
    --resolve kvm-compute-rack.home.kelch.io:443:10.32.20.10 https://kvm-compute-rack.home.kelch.io/
```

A `0` verify result means the chain and name check passed.

To return to the vendor certificate, remove the cron entry and boot script, restore `/etc/kvmd/user/backups/ssl-vendor/server.{crt,key}` into `/etc/kvmd/user/ssl/`, and reload nginx. Browsers that recorded HSTS for the hostname will then refuse it until their HSTS entry expires; use the IP address.

## NetBird

The appliance is a NetBird peer so the console is reachable away from home without the cluster or the PVE-hosted NetBird routers. It routes only its own address: the NetBird network for the console names this peer as its router and `10.32.20.10/32` as its resource, so an away client browses to the normal hostname with the trusted certificate. Away clients resolve that hostname through the site's `home-dns` resource, which the PVE routers carry. If both routers are down, browse to `https://10.32.20.10/` instead and accept the certificate-name warning; HSTS does not apply to an IP address, and the traffic still goes only through the appliance's own peer. The site's NetBird Cloud objects and policy are recorded in the [NetBird runbook](../../docs/runbooks/netbird-remote-access.md#glkvm-console-added-2026-11-11).

The firmware ships a client and its GL UI integration. Its client (0.74.7 on `V1.10.1 release2`) changes only with firmware, so a pinned upstream client runs in its place:

| Piece | Behavior |
|---|---|
| `/usr/bin/netbird` | Firmware's client. At boot the pinned client is bind-mounted over it when newer |
| `/etc/kvmd/user/bin/netbird` | Pinned upstream `linux/arm64` client, installed by [`netbird/install.sh`](netbird/install.sh) |
| `/etc/kvmd/user/scripts/S40netbird-binary` | [`netbird/S40netbird-binary`](netbird/S40netbird-binary), run at boot by `S99custom` before `S99netbird` starts the daemon. Mounts the pinned client only if its version is newer than the firmware's, so a firmware update that ships a newer client wins |
| `/etc/init.d/S99netbird` | Starts `netbird service run --daemon-addr unix:///var/run/netbird.sock` when `/etc/kvmd/user/netbird.json` has `"enable": true`; also sets IP forwarding in `/etc/sysctl.conf` |
| `/etc/kvmd/user/netbird.json` | `enable` and optional `management_url`, written by the GL UI |
| `/etc/kvmd/user/netbird/` | Peer key and state (`/var/lib/netbird` links here). Secret; kept by the firmware's update backup |
| GL UI NetBird page | Calls `netbird up --disable-dns --setup-key …`, `netbird logout` and reads status from the daemon socket |

`--disable-dns` means the appliance never uses NetBird's DNS, which it doesn't need. The appliance's kernel has WireGuard, so the client runs in kernel mode.

Nothing else changes: the GL UI and `S99netbird` call `/usr/bin/netbird` by path and talk to the same daemon socket. GL's status reader parses 0.82.1's daemon status correctly (`Connected`, IP and version). To go back to the firmware's client, delete the boot script and the pinned binary, then `umount /usr/bin/netbird` and `/etc/init.d/S99netbird restart`; the peer identity is unchanged.

### Update the client

Renovate opens a PR for each NetBird release against `NETBIRD_VERSION` in [`netbird/install.sh`](netbird/install.sh). Merging changes nothing on the appliance. Run the installer from a repository checkout:

```sh
devices/glkvm/netbird/install.sh
```

It downloads the release archive, verifies it against the release's checksum file, installs the binary and boot script, stops the daemon, waits for the old one to exit, remounts and starts it, and prints the status. NetBird access to the console drops for a few seconds; the local network path is unaffected. Do it from home or with a second way in.

Installed 2026-11-11: 0.82.1, the routers' version. The peer kept its identity and address, reconnected to management, signal and relays, and announced `Networks: 10.32.20.10/32`; NetBird's input and forward rules for the away iPhone were unchanged. The first run exposed a race, now handled by the installer: a daemon started while the previous one was still shutting down exited at once.

Enrolled 2026-11-11 as `kvm-compute-rack.netbird.cloud` with a one-off setup key whose auto-group is the console's router group; the key was revoked after its single use. The peer reported Management, Signal and Relay connected and `Networks: 10.32.20.10/32`. On the same day the iPhone, on cellular, loaded the console at its normal hostname with the trusted certificate.

A routing peer applies NetBird's resource rules to traffic it forwards. Traffic addressed to the appliance itself is local, so NetBird's input chain decides it, and that chain is filled only by a policy whose destination is the appliance's peer group. The site therefore needs both: a resource policy for the route and a peer policy for the ports. Everything else arriving on `wt0` hits NetBird's final drop.

To re-enroll after a wipe, create a new one-off setup key with the same auto-group, then either paste it into the GL UI's NetBird page or run the same steps over SSH. The key travels on standard input into a private temporary file that `--setup-key-file` reads, so it stays off both machines' command lines:

```sh
printf '%s\n' "$SETUP_KEY" | ssh root@10.32.20.10 '
    set -eu
    umask 077
    keyfile=$(mktemp)
    trap "rm -f \"$keyfile\"" EXIT
    cat > "$keyfile"
    printf "{\n    \"enable\": true\n}\n" > /etc/kvmd/user/netbird.json
    /etc/init.d/S99netbird start
    for i in 1 2 3 4 5 6 7 8 9 10; do [ -S /var/run/netbird.sock ] && break; sleep 1; done
    netbird up --disable-dns --setup-key-file "$keyfile"
    netbird status
'
```

Revoke the key afterwards, and remove the old peer from NetBird Cloud if the appliance came back with a new identity.

## Downstream KVM controls

The physical eight-port KVM switches inputs with `Left Ctrl`, `Left Ctrl`,
then the port number. GL.iNet's shortcut UI sends those events too quickly for
the switch. [`kvm-switch.py`](kvm-switch.py) writes standard eight-byte USB HID
keyboard reports with a 75 ms key hold and a 250 ms inter-key delay.

The downstream mapping is recorded below and in [`override.yaml`](override.yaml). The override retains the command channels previously used by the PiKVM Hosts menu, but the stock GL.iNet UI does not display them. Use the optional Hosts bar, physical switch buttons, or the paced SSH helper to select a host:

```sh
ssh root@10.32.20.10 '/etc/kvmd/user/bin/kvm-switch 2'
```

| KVM port | Host |
|----------|------|
| 1 | `pve-sbx-1` |
| 2 | `pve-sbx-2` |
| 3 | `pve-sbx-3` |
| 4 | `k8s-prod-1` |
| 5 | `k8s-prod-2` |
| 6 | `k8s-prod-3` |
| 7 | `spark-1` |
| 8 | `spark-2` |

All eight downstream ports are assigned. Update this table, the matching command-channel label in the override, and the host label in `hosts-prototype/index.html` together if the physical mapping changes. Always confirm the selected host on screen before sending further input.

### Optional Hosts bar

[`hosts-prototype/`](hosts-prototype/) adds eight host buttons at `https://10.32.20.10/hosts/` above an iframe containing the stock console. Sign in inside that console to enable the buttons once its video or Direct canvas exists. The page reuses the existing same-origin session cookie and protected GPIO API; it does not store credentials or add a backend service. The **Stock UI** link opens the normal root address directly.

The Hosts page coordinates playback with the stock console's input layer. While video is paused or has no current frame, it makes `#video-wrapper` ignore pointer events so the browser's native Play control can receive the click. It decides at the start of each pointer, touch, or key gesture whether that gesture belongs to the paused video, before native Play can change the playback state. That decision covers the release and click as well, including events retargeted to the sibling input overlay after playback starts. Video-targeted events reach native controls before forwarding to stock input handlers is stopped; native default actions remain available. Once video is playing with frames, new gestures use the stock input layer normally. A host-button click also calls the paused video's `play()` synchronously before sending the switch request, using the same browser gesture for both actions. The page watches for video creation, replacement, and iframe reloads, including sign-in and transfer-mode changes. These changes apply inside `/hosts/`; the vendor files and standalone `/` page are unchanged.

**Start video** remains an explicit fallback. It calls the embedded video's `play()` directly from a click, without sending input or changing transport, audio, or appliance settings. The separate playback status reports rejection, missing video, or waiting for frames without clearing a failed host-switch lock. Playback controls do not reconnect a stalled WebRTC session.

Cold-load playback diagnosis, verified 2026-10-21 in Orion: macOS **Low Power** Energy Mode blocks automatic video playback even when the site's **Allow All Auto-Play** setting is enabled. Both the stock `/` page and `/hosts/` had a visible, muted video with a live video-only MediaStream, buffered frames (`readyState: 4`), and `paused: true`; calling `play()` without a user gesture rejected with `NotAllowedError`. A separate, newly created muted video using the same stream was also rejected, ruling out a restriction specific to the stock element. Changing the Mac's plugged-in Energy Mode from **Low Power** to **Automatic** made the stock page autoplay after a hard reload and `/hosts/` autoplay on a fresh load, without calling `play()` or changing **Transfer**. Restoring **Low Power** and hard-reloading `/hosts/` reproduced the paused video and rejection. The original Low Power setting was restored after testing.

This matches WebKit's [separate low-power user-gesture restriction](https://github.com/WebKit/WebKit/blob/main/Source/WebCore/html/MediaElementSession.cpp), which applies even to muted video. WebKit also [forces a native Play control for autoplay video under this restriction](https://github.com/WebKit/WebKit/blob/main/Source/WebCore/html/HTMLMediaElement.cpp). The stock input layer covered that control and received clicks at the video's center, explaining the visible but unusable Play button. The Hosts compatibility layer fixes this interaction; it cannot remove the browser's requirement for a gesture. A normal host selection now supplies that gesture, so a separate playback action is unnecessary. The earlier onset of the symptom was not established.

Live acceptance after the compatibility change, 2026-10-21: with Low Power enabled and **WebRTC(FEC)** retained, the stock Play control started video from a physical click. After another hard reload, selecting the already-visible `k8s-prod-1` host started playback at 640×480 and issued exactly one GPIO pulse request. The video reported `paused: false` and `readyState: 4`, the input layer returned to `pointer-events: auto`, and the stock state reported HDMI signal present. The startup buttons stayed disabled until the video existed. Local browser checks verified trusted-gesture playback, isolation of media-control events from the simulated HID handler, restoration of normal input routing, video removal/reappearance, and preservation of a switch-error lockout during playback recovery.

For zero-click startup, the stock **Direct** transport also survived a hard reload of `/hosts/` and a fresh load of `/` in Low Power. It decodes H.264 with WebCodecs onto a canvas rather than playing an HTML video element, but [does not support audio](https://docs.gl-inet.com/kvm/en/user_guide/gl-rm1pe/console_guide/). The test restored **WebRTC(FEC)** afterward. **Automatic** Energy Mode also permits ordinary WebRTC autoplay on this Mac; it was tested temporarily and restored to Low Power.

The operator also confirmed that **Start video** restores playback after a hard reload without changing **Transfer**. The failure was reported in both **WebRTC** and **WebRTC(FEC)**. Changing Transfer modes also restored playback, but that stops the old video session and constructs a new one and is not needed when Start video works. Appliance capture was independently verified at 1080p60. Local browser checks covered a real paused MediaStream, rejected and pending play requests, missing/inaccessible frames, and an existing switch lockout.

Only one switch request can run at a time in a browser tab. The page reads a baseline from the authenticated `/api/streamer` endpoint, sends one GPIO pulse, and watches capture state every 250 ms for up to 12 seconds from command dispatch. After the helper reports success, it can report **Video returned** for HDMI loss followed by recovery, **Video detected** when an absent signal appears, or **Video changed** for a resolution or refresh-rate change. The resulting signal and mode must remain stable for at least 750 ms with capture online. This is evidence following the request, not active-port or host-identity readback. Confirm the hostname on screen before typing.

The toolbar keeps routine feedback on the host button: a highlight records the last request, a subtle amber pulse shows a request in progress, and a check briefly replaces the port number when a video transition is observed. No badge or persistent success pill is shown. The full result remains in the button's hover text and a screen-reader announcement; playback-success text is also visually hidden. Loading, paused-video guidance, sign-in guidance, uncertain results, and errors remain visible as compact plain text. Button sizes stay fixed, accessible names keep the port and hostname, and the pending animation respects reduced-motion preferences.

If no change appears against a valid, online baseline, the amber status says that the requested input may already be selected. Without that baseline, stable later video is unverifiable; only an actual later loss/recovery can establish a transition. Recovery too late to meet the stability window is also unverifiable. Missing video, unavailable status reads, and capture not being ready have separate messages. A completed command with an inconclusive video check unlocks switching without retrying. An interrupted, failed, or ten-second command timeout still disables switching until reload, even if video changed; video evidence never overrides an uncertain command result. Status polling stops after the check or command failure and does not run while idle. The lockout is page-local: a second tab, another session, or a reload can send its own request. The [bounded helpers](#bounded-hid-helpers) fail that request within about one second while the shared lock is busy and stop a stalled sequence with a nonzero exit instead of resuming it later. Browser cancellation does not cancel the appliance process, and no failure or timeout retracts a USB report the driver already accepted, so a failed or abandoned command may still have switched. Check the console and specific helpers before reloading. See [USB class compatibility](#usb-class-compatibility) for helper diagnosis.

Switch feedback measured and applied 2026-10-21: switching from `k8s-prod-1` to Spark 1 produced HDMI signal loss, signal recovery about 1.8 seconds later, and a capture-mode change from 640×480 to 1920×1080. The browser continued to report buffered, playing video throughout the loss, and capture `source.online` stayed true, so neither is used as loss evidence. With feedback deployed, switching back to `k8s-prod-1` reported **Video returned** at 640×480 @ 60 Hz and issued exactly one pulse. Selecting `k8s-prod-1` again produced no video transition, finished with the amber already-selected explanation, and issued one more pulse without retrying. Local checks covered loss/recovery without a resolution change, signal appearance, resolution and refresh-rate changes, formatting-only changes, short unstable changes, unavailable status, persistent signal loss, concurrent-click suppression, and observation cancellation after an HTTP failure. A command timeout remained locked even when video recovery had already been observed.

Install or update from the repository root after applying the normal appliance configuration:

```sh
devices/glkvm/hosts-prototype/install.sh
```

The installer requires the main nginx configuration to match the firmware copy, backs up any previous prototype, installs static assets under `/etc/kvmd/user/hosts-prototype/`, and adds `/usr/share/kvmd/extras/hosts-prototype/nginx.ctx-server.conf` through the firmware's existing include. It validates nginx before reloading it; no kvmd restart or USB rebuild is needed. The added route makes only these static files public, like the stock login page; `/api/gpio` and pulse requests still require authentication. Firmware updates may remove the extra include file, so reapply the installer after checking the updated stock configuration.

The Hosts location restricts framing with `X-Frame-Options: SAMEORIGIN` and CSP `frame-ancestors 'self'`; its own same-origin stock iframe remains allowed. The archived `V1.10.1 release2` nginx configuration includes HSTS from `ssl.conf` at server scope, with no framing policy there or in `gl.ctx-server.conf`. Location-level `add_header` replaces that inheritance, so the Hosts location repeats the firmware's HSTS value explicitly. Check the effective headers again before applying this include after a firmware change. This protects `/hosts/`, not the unchanged vendor `/` route; historical stock-root response headers had no framing policy.

The 2026-10-27 review corrections are repository-only: conditional media-event isolation, visible paused/loading guidance, missing-baseline handling, and Hosts framing headers have not been deployed. The 2026-10-20/21 records in this section are historical acceptance of the earlier assets. Before calling the revised page applied, verify served assets and headers, then ordinary stock keyboard/mouse input and one-command switching in supported playback modes during an authorized appliance session. GLKVM reboot persistence remains a separate, unperformed acceptance check.

Installed 2026-10-20. The operator confirmed that the embedded stock console and switching buttons work together. Live checks verified the three served assets, unchanged firmware main configuration, and unauthenticated GPIO reads and pulse requests returning 401. Local browser checks covered all eight channel mappings, concurrent-click suppression, expired-session recovery, malformed responses, and timeout lockout; desktop and phone layouts were inspected. This remains an optional prototype, with no port readback or cross-tab request lock.

To disable the prototype, remove only its nginx include, validate, and reload; the stock console remains available:

```sh
ssh root@10.32.20.10 '
    set -eu
    rm -f /usr/share/kvmd/extras/hosts-prototype/nginx.ctx-server.conf
    nginx -t -p /etc/kvmd/nginx -c /etc/kvmd/nginx-kvmd.conf \
        -g "pid /run/kvmd/nginx-test.pid; user root; error_log stderr;"
    kill -HUP "$(cat /run/kvmd/nginx.pid)"
'
```

### USB class compatibility

On `V1.10.1 release2`, leaving `otg.devices.camera.enabled` true makes the entire USB gadget advertise class/subclass/protocol `EF/02/01` (an IAD composite device), even when no USB camera function is linked. The downstream switch's Hotkey port configures this device but does not consume keyboard or mouse reports. Physical channel buttons and HDMI video still work. [`override.yaml`](override.yaml) disables this unused USB camera capability so a freshly created gadget uses `00/00/00` and its interfaces identify themselves as HID. HDMI capture is separate and unaffected by this setting. Keyboard and both mice remain enabled; virtual media remains disabled through `user/boot.yaml`.

Verified 2026-10-20: the original class accepted zero empty keyboard reports, changing only those three class bytes allowed 12 successive reports, restoring the original class reproduced the failure, and returning to `00/00/00` restored it again. The existing switch helper then selected port 2 (`pve-sbx-2`), its host enumerated all three HID interfaces, and paced typing appeared at its login prompt and was erased without submission. Rebuilding the gadget from the saved override also produced `00/00/00`; switching between ports 1 and 2 worked, and pve-sbx-2 recorded a Right-arrow press and release in its input events after the rebuild. The Logitech USB identity was unchanged. An appliance reboot was not part of this validation; recheck the class bytes, linked functions, and actual input after the next appliance reboot. The exact time the incompatible setting first appeared is unknown.

Changing the override requires rebuilding the USB gadget; restarting only kvmd does not change its USB descriptors. Run **Apply** below to install the override and rebuild the gadget. Stop kvmd and wait for its HID workers to exit before tearing down the gadget: rebuilding while they held open HID devices produced a kernel reference-count warning on this firmware. This briefly disconnects keyboard and mouse from the selected host and drops open console sessions. No host reboot is needed. After applying, check the live class bytes and linked functions:

```sh
ssh root@10.32.20.10 '
    cat /sys/kernel/config/usb_gadget/rockchip/bDeviceClass
    cat /sys/kernel/config/usb_gadget/rockchip/bDeviceSubClass
    cat /sys/kernel/config/usb_gadget/rockchip/bDeviceProtocol
    ls -l /sys/kernel/config/usb_gadget/rockchip/configs/b.1/
'
```

Expect `0x00` three times and only `hid.usb0`, `hid.usb1`, and `hid.usb2` linked. Then verify actual channel selection and one harmless key at a safe screen. USB state `configured`, an existing `/dev/hidg0`, and healthy Prometheus metrics do not prove that input works.

If switch clicks hang, inspect `ps -ef` and each helper's `/proc/<pid>/wchan`. An older, unbounded helper blocked in `f_hidg_write` can hold `/run/kvmd-kvm-switch.lock` indefinitely while later clicks queue behind it. Stop those specific `kvm-switch` processes before reconnecting USB so their remaining userspace sequences cannot resume after recovery. The [bounded helpers](#bounded-hid-helpers) installed 2026-10-28 stop such a stall with a nonzero exit within their userspace deadlines; restoring the older helpers restores the unbounded wait. Power-cycling the switch, rebooting the GLKVM, changing USB identity, and unlinking the mice did not restore input while the class was `EF/02/01`.

## Remote console helpers

The repository also preserves the standalone tools used for unattended BIOS,
boot-menu, and console work:

- [`hid-type.py`](hid-type.py) sends named keys, paced sequences, literal text,
  and Ctrl-Alt combinations through `/dev/hidg0`.
- [`hid-hold-key.py`](hid-hold-key.py) repeatedly reasserts one key through USB
  re-enumeration during POST. The proven HP Startup Menu sequence is Esc
  (`0x29`) for 30 seconds; F9/F10/F12 are `0x42`/`0x43`/`0x45`.
  That sequence was proven with the older helpers; a POST hold with the [bounded helpers](#bounded-hid-helpers) is not yet accepted ([#419](https://github.com/kelchm/home-lab/issues/419)).
- [`capture-screen.sh`](capture-screen.sh) copies a JPEG of the selected port's screen to the operator workstation, with or without a viewer open.

For the end-to-end HP Startup Menu, PXE, and PVE installation sequence, use the [PVE node bootstrap runbook](../../docs/runbooks/pve-node-bootstrap.md). For DGX host networking and recovery, use the [DGX Spark bring-up runbook](../../docs/runbooks/dgx-spark-bringup.md). This README remains the appliance configuration and recovery source.

The GLKVM starts `ustreamer` only while an authenticated KVM viewer is active and stops it about ten seconds after the final viewer disconnects. The helper reads kvmd's `/run/kvmd/ustreamer.sock` while that stream is live. Otherwise it runs its own `ustreamer` on `/dev/video0` for a few seconds, with the same capture settings and HDMI-switch signals as kvmd's, takes one frame and stops it. A viewer opened during that window can't get video until the helper exits. Without a viewer, the helper gives up after about eight seconds and reports `no HDMI signal on the selected port` when the port is dark: a host in POST or shutdown, or a console that has blanked, which a harmless key such as `hid-type esc` wakes. It removes partial JPEGs when a capture fails.

The Python helpers are installed in `/etc/kvmd/user/bin`, which normally
survives a firmware update as the appliance's persistent user partition. It is
not assumed to survive a factory reset or every future update; Git remains
authoritative. Convenience copies in `/tmp` are not authoritative and disappear
on reboot. All HID tools share `/run/kvmd-kvm-switch.lock`; do not run two input
sequences concurrently.

<a id="bounded-hid-helper-candidate"></a>

### Bounded HID helpers

Repository code shares [`hid_input.py`](hid_input.py) between all three helpers. Install the module beside the extensionless helper executables in `/etc/kvmd/user/bin/`. It uses Python 3.12's standard library, retains the existing CLI/channel commands, keeps the same advisory lock inode, and opens the HID descriptor with `O_NONBLOCK`.

Deadlines use monotonic time from each helper's `main()` entry, not from an HTTP request or eventual lock acquisition. Lock acquisition, device open, and the first report share a one-second start deadline. Every normal report has at most one second for slot readiness, nonblocking submission, and subsequent slot readiness. A cumulative deadline allows two seconds of overhead, plus the deliberate pauses already performed and 50 ms per completed report slot; retries never reset it. This leaves normal bus/processing time for long text and POST holds while preventing a fresh one-second allowance from accumulating at every stalled record. Final cleanup gets a separate 250 ms window and can submit only an all-zero release report. Short writes are errors, never retried as a report tail. Any failure stops the remaining sequence, returns nonzero, and has no automatic retry or reopen. SIGTERM and SIGHUP unwind through the same bounded cleanup as Ctrl-C. Cleanup failure is reported without hiding the original failure, and both descriptors are closed.

A report-slot stall longer than one second stops a switch or hold with a nonzero exit, even if an earlier report already acted. Holding a key through a real POST remains unaccepted on these helpers; observe it during the next separately authorized host-reboot/POST window, with exit status, elapsed time and on-screen results recorded under [#419](https://github.com/kelchm/home-lab/issues/419). Do not reboot a host solely for this acceptance.

Under normal scheduling and the documented driver behavior, total helper time is bounded by `P + 2 + 0.05*N + 0.25` seconds, where `P` is the deliberate pause time and `N` counts normal report slots completed before cleanup. A switch has `P=0.725` and at most seven such reports, so its conservative userspace allowance is 3.325 seconds. Typing `K` keys with explicit sleeps totaling `S` has `P=0.225*K+S`, `N<=1+2*K`, and a bound of `2.30+0.325*K+S`. Hold repeats still use 400 ms down / 100 ms released cycles for the requested monotonic duration, including the final cycle; a 25-second hold permits up to 50 cycles and a conservative 32.25-second allowance. Lock contention fails within one second from helper entry. A completely unavailable first slot returns within approximately 1.25 seconds including unsuccessful release cleanup. These are userspace deadline limits, not hard real-time guarantees against arbitrary kernel/filesystem faults, scheduler stalls, or process suspension.

The boundary matters: Rockchip's [Linux 6.1 HID implementation](https://github.com/rockchip-linux/kernel/blob/77168c8d5ab82399f65a80e9f807b50ba37cf483/drivers/usb/gadget/function/f_hid.c#L419-L550) returns `EAGAIN` for an occupied slot under `O_NONBLOCK`, then queues writes asynchronously with `usb_ep_queue`. Its completion callback clears `write_pending` even on completion error; `POLLOUT` establishes slot readiness, **not host delivery or a completed switch**. Its file release does not cancel a queued USB report. This is primary-source reasoning about that driver family, not a new verification of the exact shipped appliance kernel. Helper expiry stops further non-neutral userspace input, but close, timeout, and HTTP abort cannot retract an already accepted USB report. Failure after submission is ambiguous; verify the selected host and key state before any new request. Neutral cleanup is best effort and cannot guarantee release while USB is unavailable.

Offline regression tests use a one-slot asynchronous fake driver that deliberately retains accepted reports after close, virtual clocks, temporary lock files, and a full nonblocking pipe. They never open a real HID or USB interface:

```sh
python3 -m unittest discover -s devices/glkvm -p 'test_hid_input.py' -v
```

Installed 2026-10-28 at 11:44:10 UTC from signed commit `b3583d39367c6dcce76b28a7588509e93ec2bc11` ([#1088](https://github.com/kelchm/home-lab/pull/1088)) under [#928](https://github.com/kelchm/home-lab/issues/928), using the procedure below. All four installed hashes matched the reviewed files, the module and helpers are `root:root` `0644`/`0755`, and the original lock file was retained. The run-specific backup of the three previous helpers is `/etc/kvmd/user/backups/hid-deadlines.urahshkl`; the module was previously absent. Live acceptance was one switch to port 2 (`pve-sbx-2`), which exited 0 in 1.02 seconds with its hostname and `10.32.20.22:8006` confirmed on screen, and one Right-arrow, which exited 0 in 0.46 seconds; the host's input events recorded one press and release with no keys held before or after, and the login prompt stayed unsubmitted. No service restart, USB rebuild or reboot was involved. The 32 offline tests pass locally and in Linux CI. Lock-contention and USB-fault behavior has not been exercised live.

For an authorized helper-only rollout or reinstall, first freeze all input request sources and inspect/stop old queued helper processes at a harmless host screen. Stage and compile all four files, take a run-specific backup of the three executables and any existing module, then acquire `/run/kvmd-kvm-switch.lock` with `LOCK_EX|LOCK_NB` before changing files; stop if busy. Install the module first and replace each helper from a staged file while retaining that same lock inode. Check imports and hashes before releasing the lock. No kvmd restart, USB rebuild, firmware change, or host reboot is needed. Normal switching and one harmless key require separate live acceptance, with on-screen host confirmation; do not switch away from a Spark during its early-boot window described in the [Spark runbook](../../docs/runbooks/dgx-spark-bringup.md#updating-dgx-os-and-firmware). Rollback restores all three helpers and the module from that exact backup under the same quiescent/locked conditions, removing the module only if it was previously absent. Restoring old helpers also restores their unbounded-wait behavior. Optional Hosts/native Play/Direct/USB acceptance remains in [#419](https://github.com/kelchm/home-lab/issues/419).

## Monitoring

vmagent in `k8s-prod` scrapes kvmd's native export at `https://10.32.20.10/api/export/prometheus/metrics` every 30 seconds as `job="glkvm-kvmd"`, `instance="glkvm"`, `platform="glkvm"`. The scrape and alert rules are in [`glkvm.yaml`](../../kubernetes/apps/observability/victoria-metrics-k8s-stack/app/glkvm.yaml) and the **GLKVM / Recovery console** dashboard is in Grafana's `Platforms/GLKVM` folder. Alerts route like every other alert; see the [alerting runbook](../../docs/runbooks/alerting.md).

Applied 2026-10-17 on `V1.10.1 release2`. Before the patch, the unauthenticated metrics path returned HTTP 500; after it, the path returns the metrics below while `/api/info` still returns 401, vmagent reports the target up, and `GLKVMMetricsMissing` cleared. After restoring the stock UI on 443 on 2026-10-20, those HTTP checks still pass and no mass-storage function is linked. `/etc/init.d/S98kvmd restart` prints `Stopping kvmd: FAIL` because it checks for the old process before it has exited; one kvmd daemon was running afterwards.

The appliance needs two changes. **Apply** installs the override with the rest of the console configuration; the export patch is installed separately, below, so a firmware change that invalidates it never blocks restoring the console.

- [`override.yaml`](override.yaml) sets upstream kvmd's `prometheus.auth.enabled: false`, which exempts only the metrics path from login. kvmd has no read-only role: any account that could read the metrics could also type into every host's BIOS, toggle virtual media and upgrade firmware. Leaving the path unauthenticated keeps every console credential out of the cluster, at the cost of letting anyone who can reach port 443 read the appliance's temperature, CPU, memory and network counters, and the ATX and GPIO rows the scrape drops. Every other path, including `/api/info`, still requires login.
- [`export.py`](export.py) fixes the export. The vendor module asks for a `fan` source that this firmware does not register, so every request fails with HTTP 500. The replacement shadows the vendor's sourceless `export.pyc` and reproduces its bytecode except for that request. It carries the SHA-256 of the `export.pyc` it was verified against and runs the vendor module unchanged when that file differs, so a firmware update cannot leave stale code in place. The install below refuses a different `export.pyc`.

What the export provides on this firmware:

| Metric | Meaning |
|---|---|
| `pikvm_hw_temp_cpu` | SoC temperature in °C. The SoC's only thermal trip is critical, at 115 °C, where the kernel shuts the appliance down. |
| `pikvm_hw_cpu_percent` | CPU in use since kvmd's previous health reading. |
| `pikvm_hw_mem_percent`, `_total`, `_available` | Memory. The appliance has about 1 GiB, plus a compressed swap device in RAM (`/dev/zram0`, about 490 MiB) that this firmware adds. |
| `pikvm_hw_net_bytes_sent`, `_recv` | Bytes on every interface except loopback since boot; use `rate()`. |

The scrape drops `pikvm_atx_*` and `pikvm_gpio_*`. ATX is not wired, so its power state means nothing. The GPIO rows are the retained host-switch command channels, which only pulse a switch sequence; they do not read back the selected port, and the vendor code reports each channel's state under both its `online` and `state` names. The export has no HDMI signal, USB enumeration, selected port, uptime or filesystem metrics, and nothing in it observes the host on the selected port. A blank screen or missing video is not an appliance failure. Load average stays between 8 and 10 because of the SoC's media threads and is not a useful signal.

The scrape addresses the appliance by IP so it does not depend on DNS, and therefore skips certificate verification. Nothing currently alerts on certificate expiry; see [TLS](#tls).

| Alert | Fires when |
|---|---|
| `GLKVMMetricsMissing` | The scrape fails or is missing for 10 minutes. |
| `GLKVMTemperatureHigh` | The SoC is above 95 °C for 15 minutes. |
| `GLKVMMemoryLow` | Memory is over 90% used for 30 minutes. |
| `GLKVMHealthMetricsMissing` | The export answers without temperature or memory for 30 minutes, so the rule that reads the missing metric cannot fire. |

When `GLKVMMetricsMissing` fires, the status of an unauthenticated request separates the causes:

```sh
curl -sk -o /dev/null -w '%{http_code}\n' https://10.32.20.10/api/export/prometheus/metrics
```

| Status | Cause |
|---|---|
| 200 | The endpoint answers this client; check vmagent's target status and its network policy. It says nothing about video or keyboard. |
| 401 | The `prometheus` override is missing, usually after a firmware update. |
| 500 | The export patch is missing or stood aside for a different `export.pyc`. |
| 502 | kvmd is down; nginx still answers. |
| No connection | The appliance or its network. |

Install the export patch after **Apply**. It stops before changing anything unless the appliance's `export.pyc` is the one the patch was verified against and `export.py` is absent or a previous copy of the patch:

```sh
scp -O devices/glkvm/export.py root@10.32.20.10:/tmp/export.py
ssh root@10.32.20.10 '
    set -eu
    api=/usr/lib/python3.12/site-packages/kvmd/apps/kvmd/api
    expected=$(sed -n "s/^_VENDOR_PYC_SHA256 = \"\([0-9a-f]*\)\"$/\1/p" /tmp/export.py)
    actual=$(sha256sum "$api/export.pyc" | cut -d" " -f1)
    test -n "$expected"
    test "$actual" = "$expected"
    test ! -e "$api/export.py" || grep -q "^_VENDOR_PYC_SHA256 = " "$api/export.py"
    cp /tmp/export.py "$api/export.py"
    rm -f /tmp/export.py
    /etc/init.d/S98kvmd restart
'
```

Restarting kvmd drops open console sessions for a few seconds; do it while no host needs the console. The file lives in the root overlay, like `/etc/kvmd/override.yaml`, so it survives a reboot. Whether a firmware update keeps it is not verified; the hash check makes either outcome safe. Then confirm the export and that everything else still requires login:

```sh
ssh root@10.32.20.10 '
    set -eu
    /etc/init.d/S98kvmd status
    curl -ksS https://127.0.0.1/api/export/prometheus/metrics | grep -q "^pikvm_hw_temp_cpu "
    test "$(curl -ksS -o /dev/null -w "%{http_code}" https://127.0.0.1/api/info)" = 401
'
```

In VictoriaMetrics, `up{job="glkvm-kvmd"}` is 1 within a minute of the vmagent policy and scrape being live.

To remove monitoring from the appliance, delete the `prometheus` block from `/etc/kvmd/override.yaml`, then `rm /usr/lib/python3.12/site-packages/kvmd/apps/kvmd/api/export.py` and `/etc/init.d/S98kvmd restart`. The metrics path then requires login again and returns HTTP 500 once logged in, as shipped.

## Apply

The appliance's Dropbear server does not provide an SFTP subsystem, so force
legacy SCP mode with `-O`:

```sh
(
set -e
ssh root@10.32.20.10 'command -v timeout >/dev/null && command -v pkill >/dev/null && command -v pgrep >/dev/null'
scp -O devices/glkvm/kvm-switch.py root@10.32.20.10:/tmp/kvm-switch
scp -O devices/glkvm/hid-type.py root@10.32.20.10:/tmp/hid-type
scp -O devices/glkvm/hid-hold-key.py root@10.32.20.10:/tmp/hid-hold-key
scp -O devices/glkvm/hid_input.py root@10.32.20.10:/tmp/hid_input.py
scp -O devices/glkvm/override.yaml root@10.32.20.10:/tmp/override.yaml

ssh root@10.32.20.10 '
    set -eu
    mkdir -p /etc/kvmd/user/bin /etc/kvmd/user/backups
    if [ -e /etc/kvmd/override.yaml ]; then
        cp /etc/kvmd/override.yaml \
            /etc/kvmd/user/backups/override.yaml.before-kvm-controls
    fi
    cp /tmp/hid_input.py /etc/kvmd/user/bin/hid_input.py
    chmod 0644 /etc/kvmd/user/bin/hid_input.py
    cp /tmp/kvm-switch /etc/kvmd/user/bin/kvm-switch
    cp /tmp/hid-type /etc/kvmd/user/bin/hid-type
    cp /tmp/hid-hold-key /etc/kvmd/user/bin/hid-hold-key
    chmod 0755 /etc/kvmd/user/bin/kvm-switch \
        /etc/kvmd/user/bin/hid-type /etc/kvmd/user/bin/hid-hold-key
    cp /tmp/override.yaml /etc/kvmd/override.yaml
    if [ -e /etc/kvmd/nginx-kvmd.conf ] && \
        [ ! -e /etc/kvmd/user/backups/nginx-kvmd.conf.before-stock-ui ]; then
        cp /etc/kvmd/nginx-kvmd.conf \
            /etc/kvmd/user/backups/nginx-kvmd.conf.before-stock-ui
    fi
    nginx -t -p /etc/kvmd/nginx -c /rom/etc/kvmd/nginx-kvmd.conf \
        -g "pid /run/kvmd/nginx-test.pid; user root; error_log stderr;"
    cp /rom/etc/kvmd/nginx-kvmd.conf /etc/kvmd/nginx-kvmd.conf
    rm -f /tmp/kvm-switch /tmp/hid-type /tmp/hid-hold-key \
        /tmp/hid_input.py /tmp/override.yaml
    restore_kvmd() {
        if ! pgrep -f "[k]vmd/main" >/dev/null; then
            /etc/init.d/S98kvmd start
        fi
    }
    trap restore_kvmd EXIT
    # Discard queued input before USB returns. SIGKILL skips blocked cleanup writes.
    helpers="^[^ ]*python3 /etc/kvmd/user/bin/(kvm-switch|hid-type|hid-hold-key)( |$)"
    /etc/init.d/S98kvmd stop
    workers="[k]vmd/(main|hid-|streamer)|$helpers"
    for attempt in 1 2 3 4 5 6 7 8 9 10; do
        pkill -KILL -f "$helpers" || true
        pgrep -f "$workers" >/dev/null || break
        sleep 1
    done
    if pgrep -f "$workers" >/dev/null; then
        echo "kvmd workers have not exited; USB gadget was not rebuilt" >&2
        exit 1
    fi
    timeout 15 /usr/bin/kvmd-otg stop
    /usr/bin/kvmd-otg start
    /etc/init.d/S98kvmd start
    trap - EXIT
    if [ -f /run/kvmd/pikvm-nginx.pid ]; then
        kill "$(cat /run/kvmd/pikvm-nginx.pid)" 2>/dev/null || true
    fi
    /etc/init.d/S99kvmd-nginx restart
'
)
```

Confirm `kvmd` restarted, the stock UI responds, and only the expected HID functions are linked. The port-8888 probe below checks only IPv4 loopback HTTPS; failure is not proof that all TCP listeners are absent. The capture helper works with or without an authenticated viewer:

```sh
ssh root@10.32.20.10 '
    set -eu
    /etc/init.d/S98kvmd status
    cmp /rom/etc/kvmd/nginx-kvmd.conf /etc/kvmd/nginx-kvmd.conf
    curl -ksS https://127.0.0.1/ | grep -q "<title>GLKVM</title>"
    if curl -ksS --connect-timeout 2 --max-time 3 https://127.0.0.1:8888/ >/dev/null 2>&1; then
        echo "Unexpected IPv4 HTTPS response on port 8888" >&2
        exit 1
    fi
    for field in bDeviceClass bDeviceSubClass bDeviceProtocol; do
        test "$(cat /sys/kernel/config/usb_gadget/rockchip/$field)" = 0x00
    done
    python3 -c "from pathlib import Path; assert sorted(p.name for p in Path(\"/sys/kernel/config/usb_gadget/rockchip/configs/b.1\").iterdir() if p.is_symlink()) == [\"hid.usb0\", \"hid.usb1\", \"hid.usb2\"]"
    test -c /dev/hidg0
    python3 -m py_compile /etc/kvmd/user/bin/kvm-switch \
        /etc/kvmd/user/bin/hid-type /etc/kvmd/user/bin/hid-hold-key \
        /etc/kvmd/user/bin/hid_input.py
'
devices/glkvm/capture-screen.sh /tmp/kvm-snap.jpg
```

Only send a live HID smoke-test key when the attached host is at a screen where
that input is harmless. For example:

```sh
ssh root@10.32.20.10 '/etc/kvmd/user/bin/hid-type down'
```

## Firmware-update checklist

Before an appliance update, record the running firmware and back up the applied
state from `/etc/kvmd/override.yaml`, `/etc/kvmd/nginx-kvmd.conf`, and
`/etc/kvmd/user/` outside the appliance. One simple workstation-side backup is:

```sh
backup_dir=$(mktemp -d)
chmod 0700 "$backup_dir"
scp -O -r root@10.32.20.10:/etc/kvmd/user "$backup_dir/"
scp -O root@10.32.20.10:/etc/kvmd/override.yaml "$backup_dir/"
scp -O root@10.32.20.10:/etc/kvmd/nginx-kvmd.conf "$backup_dir/"
```

`/etc/kvmd/user` holds secrets: the Cloudflare token, Lego's account and certificate keys, NetBird's peer key, the login password hashes and the TOTP secret. Keep this backup private and delete it once the update is verified; never commit it.

After the update:

1. Confirm SSH, `/dev/hidg0`, and `/run/kvmd/ustreamer.sock` exist, and that `ls /sys/kernel/config/usb_gadget/rockchip/configs/*/` links no `mass_storage` function.
2. Keep the firmware's stock nginx configuration; do not restore the old PiKVM web configuration from a backup.
3. Re-run **Apply** to restore the known files and modes. The USB gadget rebuild applies the camera-disable override; confirm the three device class bytes are `00/00/00` as described under **USB class compatibility**.
4. Run the non-interactive checks above and capture a screen.
5. With a host at a safe menu, verify one paced HID key and one downstream KVM
   channel switch.
6. Recheck the [monitoring](#monitoring) patch against the new firmware's `export.pyc` before reinstalling it. The installed copy steps aside on its own when that file changes, and its install refuses a module it was not verified against.
7. Confirm the [name and address](#name-and-address) survived: `hostname` prints `kvm-compute-rack` and connman still reports the static address (`connmanctl services ethernet_9483c4c499d7_cable | grep IPv4`).
8. Confirm [TLS](#tls) survived: the renewal entry is in `/etc/crontabs/root` (the boot script restores it), and the `curl` check in that section returns `0`. If the firmware dropped `/etc/kvmd/user/scripts` or the cron entry, rerun `devices/glkvm/acme/install.sh`, then reinstall the current certificate with `ssh root@10.32.20.10 /etc/kvmd/user/acme/deploy.sh kvm-compute-rack.home.kelch.io`. If nginx's `ssl.conf` no longer reads `/etc/kvmd/user/ssl`, stop: update the path in `acme/deploy.sh` to match the new firmware before installing or deploying, because the hook writes only to that directory.
9. Confirm [NetBird](#netbird) survived: `netbird status` shows Management connected and `Networks: 10.32.20.10/32`, and `netbird version` reports the pinned version or a newer firmware one. If `/etc/kvmd/user/scripts/S40netbird-binary` or the pinned binary is gone, rerun `devices/glkvm/netbird/install.sh`.

The update to `V1.10.1 release2` kept `/etc/kvmd/user`, the mass-storage settings in `user/boot.yaml`, and `/etc/kvmd/override.yaml`, but replaced `/etc/kvmd/nginx-kvmd.conf` with the vendor's, which serves only the GLKVM UI on 443.

The repository is the recovery source even if a firmware update wipes both
`/etc/kvmd/user` and `/etc/kvmd`.

### Bootstrap after a wipe

Restore the appliance's network configuration and SSH access through the vendor UI first. Confirm `ssh root@10.32.20.10` works; this firmware uses Dropbear, so the `scp -O` commands in **Apply** are intentional. Then run the complete **Apply** procedure from a repository checkout. It recreates the user directories, installs all three HID tools and their shared `hid_input.py` module, restores the committed appliance override and the installed firmware's stock nginx configuration, and restarts the affected services. Finish with the non-interactive checks and safe live-input test above, then install the [monitoring](#monitoring) export patch. Then set the [name and address](#name-and-address) in the GL UI, and reinstall [TLS](#tls): run its installer, install the token and run `renew.sh`. Without the old `lego-state/` this creates a new Let's Encrypt account and orders a new certificate, which is fine. Finally restore [NetBird](#netbird): run `devices/glkvm/netbird/install.sh`, re-enroll with a new one-off setup key as described there, confirm `netbird status` shows Management connected and `Networks: 10.32.20.10/32`, and remove the old peer from NetBird Cloud.

Do not run `kvmd` itself over SSH, including `kvmd -M` to validate configuration. Unlike upstream PiKVM, firmware `V1.9.1 release1` started another daemon instead of performing a validation-only run.

## Recovery

For a missing keyboard or hung channel switches with USB class `EF/02/01`, use **Apply** to install the camera-disable override and rebuild the USB gadget. The kvmd-only restarts below recover the daemon's configuration; they do not change USB descriptors. If gadget teardown fails or times out, stop and inspect its log and remaining configfs state; the Apply exit trap restarts kvmd if it is not running, but cannot repair a partially removed USB gadget.

If `kvmd` does not start and the appliance backup survived, restore it:

```sh
ssh root@10.32.20.10 '
    cp /etc/kvmd/user/backups/override.yaml.before-kvm-controls \
        /etc/kvmd/override.yaml
    /etc/init.d/S98kvmd restart
'
```

That backup may predate the camera-disable fix. Before another appliance reboot or USB rebuild, run **Apply** or ensure the restored override contains `otg.devices.camera.enabled: false`. A kvmd restart alone does not update the USB descriptors. Verify the live class bytes and actual input after the next reboot.

If the appliance backup did not survive, upload the committed override instead:

```sh
scp -O devices/glkvm/override.yaml root@10.32.20.10:/tmp/override.yaml
ssh root@10.32.20.10 '
    cp /tmp/override.yaml /etc/kvmd/override.yaml
    rm -f /tmp/override.yaml
    /etc/init.d/S98kvmd restart
'
```

If nginx does not start, restore the vendor configuration:

```sh
ssh root@10.32.20.10 '
    cp /rom/etc/kvmd/nginx-kvmd.conf /etc/kvmd/nginx-kvmd.conf
    /etc/init.d/S99kvmd-nginx restart
'
```

The helpers live on the persistent user partition. Firmware upgrades may still replace `/etc/kvmd/override.yaml` or change the vendor nginx configuration. After an upgrade, keep the new firmware's stock nginx file and recheck the USB compatibility and monitoring overrides before restoring them.
