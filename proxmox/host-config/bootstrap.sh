#!/bin/bash
# Explicit workstation/SSH operation; payload delivery never runs this script.
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'run as root' >&2; exit 1; }
[[ $# == 2 || $# == 4 ]] || { echo 'usage: bootstrap.sh REPOSITORY BRANCH [--guest-host-id pve-sbx-1]' >&2; exit 1; }
repository=$1 branch=$2 host_id=$(hostname -s)
source_dir=$(cd "$(dirname "$0")/engine" && pwd)
. /etc/os-release
[[ $ID == debian && $VERSION_ID == 13 ]] || { echo 'requires Debian 13 / PVE 9' >&2; exit 1; }
if [[ $# == 4 ]]; then
    [[ $3 == --guest-host-id && $host_id == monitoring-eval-990 && ! -d /etc/pve ]] || exit 1
    host_id=$4
else
    [[ -d /etc/pve ]] || { echo 'non-PVE requires the explicit disposable guest override' >&2; exit 1; }
fi
[[ $host_id =~ ^pve-sbx-[123]$ && $branch =~ ^[a-zA-Z0-9][a-zA-Z0-9/_-]*$ ]] || exit 1
# Refuse implicit adoption of an occupied unit, even without a Debian package.
if [[ ! -f /etc/pve-hostcfg/config.json ]]; then
    exporter_runtime=$(timeout 10 systemctl show prometheus-node-exporter.service --property=LoadState --property=ActiveState 2>/dev/null) || {
        echo 'exporter activity is unknown; ownership must be reviewed' >&2
        exit 1
    }
    if dpkg-query -W -f='${Status}' prometheus-node-exporter 2>/dev/null | grep -q 'ok installed' ||
        [[ -e /etc/systemd/system/prometheus-node-exporter.service || -L /etc/systemd/system/prometheus-node-exporter.service ]] ||
        ! grep -qxF 'LoadState=not-found' <<< "$exporter_runtime" ||
        ! grep -qxF 'ActiveState=inactive' <<< "$exporter_runtime"; then
        echo 'exporter already exists or activity is unknown; ownership must be reviewed' >&2
        exit 1
    fi
fi
install -d -m 0755 /var/lib/pve-hostcfg /var/lib/pve-hostcfg/textfile
printf '%s\n' 'explicit bootstrap pause' > /var/lib/pve-hostcfg/paused
systemctl stop pve-hostcfg.timer pve-hostcfg.service 2>/dev/null || true
# Settle a recorded transient play before package or engine changes. A killed
# manual controller can release its flock while its bounded play is still alive.
if [[ -f /etc/pve-hostcfg/config.json ]]; then
    "$source_dir/controller.py" pause
fi
exec 9>/var/lib/pve-hostcfg/lock
flock -n 9 || { echo 'controller still running' >&2; exit 1; }
# Record pending reconvergence before replacing any part of an existing engine.
if [[ -f /etc/pve-hostcfg/config.json ]]; then
    python3 - "$source_dir/controller.py" <<'PYDIRTY'
import importlib.util, json, sys
from pathlib import Path
sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location('pve_hostcfg_bootstrap', sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
controller = module.Controller(json.loads(Path('/etc/pve-hostcfg/config.json').read_text()))
controller.s['dirty'] = 1
controller.report()
PYDIRTY
fi
apt-get update
apt-get install -y --no-install-recommends --no-remove git=1:2.47.3-0+deb13u1 ansible-core=2.19.11-0+deb13u1
python3 - "$source_dir/controller.py" "$source_dir/validate-unit" "$source_dir/publish-unit" <<'PYCODE'
import pathlib, sys
for path in sys.argv[1:]:
    compile(pathlib.Path(path).read_text(), path, 'exec')
PYCODE
ANSIBLE_CONFIG="$source_dir/ansible.cfg" /usr/bin/ansible-playbook "$source_dir/node-exporter.yml" -i localhost, -c local --syntax-check
systemd-analyze verify "$source_dir/pve-hostcfg.service" "$source_dir/pve-hostcfg.timer" 2>/dev/null || {
    # verify needs the not-yet-installed ExecStart path; verify using a staged path.
    stage=$(mktemp -d /tmp/pve-hostcfg-verify.XXXXXXXX)
    trap 'rm -rf "$stage"' EXIT
    install -m 0755 "$source_dir/controller.py" "$stage/controller.py"
    sed "s|/usr/local/lib/pve-hostcfg/controller.py|$stage/controller.py|" "$source_dir/pve-hostcfg.service" > "$stage/pve-hostcfg.service"
    cp "$source_dir/pve-hostcfg.timer" "$stage/"
    systemd-analyze verify "$stage/pve-hostcfg.service" "$stage/pve-hostcfg.timer"
}
install -d -m 0755 /usr/local/lib/pve-hostcfg /etc/pve-hostcfg
for file in controller.py validate-unit publish-unit; do
    install -m 0755 "$source_dir/$file" "/usr/local/lib/pve-hostcfg/$file"
done
for file in ansible.cfg node-exporter.yml node-exporter.service.j2; do
    install -m 0644 "$source_dir/$file" "/usr/local/lib/pve-hostcfg/$file"
done
python3 - "$repository" "$branch" "$host_id" <<'PY'
import importlib.util, json, sys
from pathlib import Path
config = dict(repository=sys.argv[1], branch=sys.argv[2], host_id=sys.argv[3],
              state_dir='/var/lib/pve-hostcfg', engine_dir='/usr/local/lib/pve-hostcfg',
              textfile_dir='/var/lib/pve-hostcfg/textfile', fetch_timeout=45, apply_timeout=300)
path = Path('/etc/pve-hostcfg/config.json')
tmp = path.with_suffix('.tmp')
tmp.write_text(json.dumps(config) + '\n')
tmp.chmod(0o644)
tmp.replace(path)
sys.dont_write_bytecode = True
# Explicit engine installation requires one reconvergence after accepted resume,
# even when the fetched data is the same previously successful payload.
spec = importlib.util.spec_from_file_location('pve_hostcfg_bootstrap', '/usr/local/lib/pve-hostcfg/controller.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
controller = module.Controller(config)
controller.s['dirty'] = 1
controller.report()
PY
install -m 0644 "$source_dir/pve-hostcfg.service" "$source_dir/pve-hostcfg.timer" /etc/systemd/system/
systemctl daemon-reload
flock -u 9
/usr/local/lib/pve-hostcfg/controller.py pause
systemctl enable --now pve-hostcfg.timer
echo 'Installed paused. Review config; explicitly resume only the approved canary.'
