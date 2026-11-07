#!/usr/bin/env python3
"""Build and run only this checkout's isolated Grafana preview. Never operates on the cluster."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.request
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

ROOT = Path(__file__).resolve().parent
def executable(name, version):
    local = Path.home() / '.local/share/mise/installs' / name / version / ('bin/node' if name == 'node' else name)
    return Path(os.environ.get(name.upper()) or (local if Path(local).is_file() else shutil.which(name) or name))


NODE = executable('node', '24.22.1')
PNPM = executable('pnpm', '12.5.1')
ENV = {**os.environ, 'PATH': f'{NODE.parent}:{PNPM.parent}:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin', 'MISE_AUTO_INSTALL': '0'}
APP = 'homelab-observability-app'
HOME = f'/a/{APP}/explore'
OWNER = hashlib.sha256(str(ROOT).encode()).hexdigest()[:16]
OWNER_LABEL = 'io.kelch.grafana-preview.owner'
LANES = ['viewer', 'test-a', 'test-b']


def run(args, cwd=ROOT, env=None, capture=False):
    result = subprocess.run([str(a) for a in args], cwd=cwd, env=env or ENV, check=True, text=True, stdout=subprocess.PIPE if capture else None)
    return result.stdout if capture else None


def json_write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + '\n')


def digest(payload):
    sha = hashlib.sha256()
    for name, content in sorted(payload.items()):
        sha.update(name.encode() + b'\0' + content + b'\0')
    return sha.hexdigest()


def files_under(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob('*')) if p.is_file()}


def immutable(base, payload):
    sha = digest(payload)
    target = base / sha
    for name, content in payload.items():
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if path.read_bytes() != content:
                raise RuntimeError(f'Immutable artifact changed: {path}')
        else:
            path.write_bytes(content)
    return target, sha


def workspace(args):
    # Never install into or replace the shared/scenes symlinks supplied by the original experiment.
    payload = {}
    for lane in ['shared', 'scenes']:
        for folder, dirs, files in os.walk(ROOT / lane, followlinks=False):
            dirs[:] = sorted(d for d in dirs if d not in {'node_modules', 'dist', 'evidence', 'out', '__pycache__'})
            for filename in sorted(files):
                p = Path(folder) / filename
                if filename == 'bundle-report.json' or filename.endswith('.tmp'):
                    continue
                payload[str(p.relative_to(ROOT))] = p.read_bytes()
    payload['runtime/queries.json'] = (ROOT / 'runtime/queries.json').read_bytes()
    target, sha = immutable(args.output_dir / 'workspaces', payload)
    for lane in ['shared', 'scenes']:
        package = target / lane
        modules = package / 'node_modules'
        if args.dependencies == 'frozen':
            # Different workspace per installation mode; a cached symlink is never replaced.
            if modules.is_symlink():
                raise RuntimeError(f'Frozen setup refuses a dependency symlink: {modules}')
            run([PNPM, 'install', '--frozen-lockfile'], cwd=package)
        elif not modules.exists():
            cached = (ROOT / lane / 'node_modules').resolve(strict=True)
            modules.symlink_to(cached, target_is_directory=True)
    return target, sha


def build(args):
    source, sha = workspace(args)
    lock = {}
    for lane in ['shared', 'scenes']:
        # Build output is disposable. Published artifacts below are never edited in place.
        out = args.output_dir / 'builds' / sha / lane
        run([NODE, 'build.mjs'], cwd=source / lane, env={**ENV, 'OUT': str(out), 'REPORT': str(out / 'bundle-report.json')})
        payload = {k: v for k, v in files_under(out).items() if k != 'bundle-report.json'}
        meta = json.loads(payload['plugin.json'])
        source_sha = digest(payload)
        source_version = meta['info']['version']
        meta['info']['version'] = source_version.split('-')[0] + '-' + source_sha[:12]
        payload['plugin.json'] = (json.dumps(meta, indent=2) + '\n').encode()
        target, artifact_sha = immutable(args.output_dir / 'artifacts' / meta['id'], payload)
        lock[meta['id']] = {'path': str(target), 'sha256': artifact_sha, 'sourceSha256': source_sha, 'moduleSha256': hashlib.sha256(payload['module.js']).hexdigest(), 'version': meta['info']['version'], 'sourceVersion': source_version, 'bytes': sum(map(len, payload.values()))}
    json_write(args.output_dir / 'plugin-lock.json', lock)
    print(json.dumps(lock, indent=2))
    return lock


def datasource_variable(v2=False):
    options = [{'text': 'Recorded replay', 'value': 'comparison', 'selected': True}, {'text': 'Live lab', 'value': 'comparison-live', 'selected': False}]
    spec = {'name': 'datasource', 'label': 'Source', 'query': 'comparison,comparison-live', 'current': {'text': 'Recorded replay', 'value': 'comparison'}, 'options': options, 'multi': False, 'includeAll': False, 'skipUrlSync': False, 'allowCustomValue': False}
    if v2:
        return {'kind': 'CustomVariable', 'spec': {**spec, 'hide': 'dontHide'}}
    return {**spec, 'type': 'custom', 'hide': 0}


def native_dashboard(original, window):
    d = copy.deepcopy(original)
    v2 = d.get('kind') == 'Dashboard'
    spec = d['spec'] if v2 else d
    title = spec.get('title', 'Dashboard')
    spec['description'] = f'Local preview copy of {title}. Recorded by default; uncaptured queries remain empty. ' + spec.get('description', '')
    spec['editable'] = False
    spec['tags'] = list(dict.fromkeys([*spec.get('tags', []), 'local-preview']))
    if v2:
        spec.setdefault('timeSettings', {}).update({'from': window['from'], 'to': window['to'], 'autoRefresh': '', 'timezone': 'America/New_York'})
        spec['variables'] = [datasource_variable(True)] + [v for v in spec.get('variables', []) if v.get('spec', {}).get('name') != 'datasource']
    else:
        d['id'] = None
        d['refresh'] = ''
        d['time'] = window
        d['timezone'] = 'America/New_York'
        d.setdefault('templating', {})['list'] = [datasource_variable()] + [v for v in d.get('templating', {}).get('list', []) if v.get('name') != 'datasource']

    def rewrite(value):
        if isinstance(value, list):
            return [rewrite(item) for item in value]
        if not isinstance(value, dict):
            return value
        value = {key: rewrite(item) for key, item in value.items()}
        ds = value.get('datasource')
        if isinstance(ds, dict) and ds.get('type') == 'prometheus':
            value['datasource'] = {**ds, 'uid': '${datasource}'}
        elif isinstance(ds, dict) and ('name' in ds) and value.get('group') == 'prometheus':
            value['datasource'] = {'name': '${datasource}'}
        elif isinstance(ds, str) and ds not in {'-- Grafana --', '-- Dashboard --', '__expr__', 'grafana'}:
            value['datasource'] = '${datasource}'
        # Native-to-native links carry the selected datasource as well as Grafana's normal time/entity variables.
        url = value.get('url')
        if isinstance(url, str) and url.startswith('/d/'):
            parsed = urlsplit(url)
            params = dict(parse_qsl(parsed.query, keep_blank_values=True))
            params['var-datasource'] = '${datasource}'
            query = urlencode(params, safe='${}:')
            value.update(url=urlunsplit(('', '', parsed.path, query, parsed.fragment)), keepTime=True, includeVars=True)
        return value
    return rewrite(d)


def native_copies(args, window):
    source = ROOT.parents[1] / 'kubernetes/apps/observability/grafana/app'
    payload, manifest = {}, []
    for file in sorted(source.rglob('*.json')):
        original = json.loads(file.read_text())
        if not ('panels' in original or original.get('kind') == 'Dashboard'):
            continue
        d = native_dashboard(original, window)
        uid = d.get('uid', d.get('metadata', {}).get('name'))
        if not uid:
            continue
        payload[f'{uid}.json'] = (json.dumps(d, indent=2) + '\n').encode()
        manifest.append({'uid': uid, 'source': str(file.relative_to(ROOT.parents[1])), 'sha256': hashlib.sha256(file.read_bytes()).hexdigest()})
        if uid == 'home':
            alias = copy.deepcopy(d)
            alias['metadata']['name'] = 'home-compare-native'
            alias['spec']['title'] = 'Home · local native copy'
            payload['home-compare-native.json'] = (json.dumps(alias, indent=2) + '\n').encode()
    payload['manifest.txt'] = (json.dumps(manifest, indent=2) + '\n').encode()
    return immutable(args.output_dir / 'native-dashboards', payload)[0]


def compose(args, lock, replay_dir, native_dir):
    plugins = {ident: item['sha256'] for ident, item in lock.items()}
    release = digest({'plugins': json.dumps(plugins, sort_keys=True).encode(), 'replay': digest(files_under(replay_dir)).encode(), 'native': digest(files_under(native_dir)).encode(), 'liveUrl': args.live_url.encode()})
    results = {}
    for lane in args.lanes:
        index = LANES.index(lane) * 2
        grafana_port, fixture_port = args.ports[index:index + 2]
        project = f'{args.project}-{lane}'
        folder = args.output_dir / 'deployments' / release / lane
        provisioning = folder / 'provisioning'
        labels = {OWNER_LABEL: OWNER, 'io.kelch.grafana-preview.lane': lane}
        state_dir, log_dir = args.state_dir / args.project / lane, args.log_dir / args.project / lane
        state_dir.mkdir(parents=True, exist_ok=True); log_dir.mkdir(parents=True, exist_ok=True)
        if not (state_dir / 'state.json').exists():
            json_write(state_dir / 'state.json', {})
        json_write(provisioning / 'datasources/data.yaml', {'apiVersion': 1, 'datasources': [
            {'name': 'Recorded replay', 'uid': 'comparison', 'type': 'prometheus', 'access': 'proxy', 'url': 'http://fixture:9090', 'isDefault': True, 'editable': False, 'jsonData': {'httpMethod': 'POST', 'timeInterval': '5m', 'prometheusType': 'Prometheus', 'prometheusVersion': '2.55.0'}},
            {'name': 'Live lab', 'uid': 'comparison-live', 'type': 'prometheus', 'access': 'proxy', 'url': args.live_url, 'editable': False, 'jsonData': {'httpMethod': 'POST', 'timeInterval': '30s', 'prometheusType': 'Prometheus', 'prometheusVersion': '2.55.0'}},
        ]})
        json_write(provisioning / 'plugins/app.yaml', {'apiVersion': 1, 'apps': [{'type': APP, 'org_id': 1, 'disabled': False}]})
        json_write(provisioning / 'dashboards/provider.yaml', {'apiVersion': 1, 'providers': [{'name': 'Local native copies', 'folder': 'Preview · native copies', 'type': 'file', 'disableDeletion': True, 'allowUiUpdates': False, 'updateIntervalSeconds': 30, 'options': {'path': '/preview-dashboards'}}]})
        data = {'name': project, 'services': {
            'fixture': {'image': args.fixture_image, 'labels': labels, 'command': ['node', '/proof/server.mjs'], 'environment': {'LANE': lane, 'STATE_DIR': '/state', 'LOG_DIR': '/logs'}, 'ports': [f'127.0.0.1:{fixture_port}:9090'], 'read_only': True, 'cap_drop': ['ALL'], 'volumes': [f'{replay_dir}:/proof:ro', f'{state_dir}:/state', f'{log_dir}:/logs']},
            'grafana': {'image': args.grafana_image, 'labels': labels, 'depends_on': ['fixture'], 'ports': [f'127.0.0.1:{grafana_port}:3000'], 'read_only': True, 'tmpfs': ['/tmp:mode=1777', '/var/lib/grafana:uid=472,gid=472,mode=0770'], 'cap_drop': ['ALL'],
                'environment': {'GF_AUTH_ANONYMOUS_ENABLED': 'true', 'GF_AUTH_ANONYMOUS_ORG_ROLE': 'Viewer', 'GF_AUTH_BASIC_ENABLED': 'false', 'GF_SECURITY_DISABLE_INITIAL_ADMIN_CREATION': 'true', 'GF_ANALYTICS_REPORTING_ENABLED': 'false', 'GF_ANALYTICS_CHECK_FOR_UPDATES': 'false', 'GF_ANALYTICS_CHECK_FOR_PLUGIN_UPDATES': 'false', 'GF_PLUGINS_PREINSTALL_DISABLED': 'true', 'GF_PATHS_PLUGINS': '/opt/plugins', 'GF_PLUGINS_ALLOW_LOADING_UNSIGNED_PLUGINS': ','.join(lock), 'GF_USERS_DEFAULT_THEME': 'dark', 'GF_AUTH_DISABLE_LOGIN_FORM': 'true', 'GF_USERS_HOME_PAGE': HOME, 'GF_FEATURE_TOGGLES_ENABLE': 'grafana.visualDesignRefresh'},
                'volumes': [f'{provisioning}:/etc/grafana/provisioning:ro', f'{native_dir}:/preview-dashboards:ro'] + [f'{item["path"]}:/opt/plugins/{ident}:ro' for ident, item in lock.items()]},
        }, 'networks': {'default': {'labels': labels}}}
        compose_file = folder / 'compose.json'
        json_write(compose_file, data)
        results[lane] = {'project': project, 'compose': str(compose_file), 'url': f'http://127.0.0.1:{grafana_port}{HOME}', 'fixture': f'http://127.0.0.1:{fixture_port}', 'state': str(state_dir), 'logs': str(log_dir), 'release': release}
    registry = args.output_dir / f'{args.project}-lanes.json'
    previous = json.loads(registry.read_text()) if registry.exists() else {}
    json_write(registry, {**previous, **results})
    return results


def check_ownership(project):
    ids = run(['docker', 'ps', '-aq', '--filter', f'label=com.docker.compose.project={project}'], capture=True).split()
    if ids:
        for item in json.loads(run(['docker', 'inspect', *ids], capture=True)):
            if item['Config'].get('Labels', {}).get(OWNER_LABEL) != OWNER:
                raise RuntimeError(f'Refusing to operate on a container not owned by this checkout: {item["Name"]}')


def available_ports(args):
    allowed = set()
    for lane in args.lanes:
        project = f'{args.project}-{lane}'
        check_ownership(project)
        ids = run(['docker', 'ps', '-q', '--filter', f'label=com.docker.compose.project={project}'], capture=True).split()
        if ids:
            for item in json.loads(run(['docker', 'inspect', *ids], capture=True)):
                for ports in item['NetworkSettings']['Ports'].values():
                    allowed.update(int(p['HostPort']) for p in (ports or []))
        for port in args.ports[LANES.index(lane) * 2:LANES.index(lane) * 2 + 2]:
            if port in allowed:
                continue
            with socket.socket() as sock:
                try: sock.bind(('127.0.0.1', port))
                except OSError: raise RuntimeError(f'Port {port} is occupied; use --ports with six free ports') from None


def wait_ready(url):
    until = time.monotonic() + 50
    while time.monotonic() < until:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
        except Exception:
            time.sleep(.5)
    raise RuntimeError(f'Preview did not become ready: {url}')


def main():
    global NODE, PNPM, ENV
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['up', 'assemble', 'build', 'setup', 'smoke', 'status', 'down', 'test'], nargs='?', default='up')
    p.add_argument('--project', default='grafana-observability-preview')
    p.add_argument('--ports', default='3431,3432,3433,3434,3435,3436', help='viewer Grafana/fixture, test-a Grafana/fixture, test-b Grafana/fixture')
    p.add_argument('--lanes', default=','.join(LANES))
    p.add_argument('--output-dir', type=Path, default=ROOT / 'runtime/generated')
    p.add_argument('--state-dir', type=Path, default=ROOT / 'runtime/state')
    p.add_argument('--log-dir', type=Path, default=ROOT / 'runtime/logs')
    p.add_argument('--evidence-dir', type=Path, default=Path(tempfile.gettempdir()) / 'grafana-preview-evidence')
    p.add_argument('--dependencies', choices=['cached', 'frozen'], default='cached')
    p.add_argument('--node', type=Path, default=NODE, help='Node executable; also configurable with NODE')
    p.add_argument('--pnpm', type=Path, default=PNPM, help='pnpm executable; also configurable with PNPM')
    p.add_argument('--live-url', default='http://host.lima.internal:38428')
    p.add_argument('--grafana-image', default='grafana/grafana:13.3.0-distroless')
    p.add_argument('--fixture-image', default='node:24.22.1-alpine')
    args = p.parse_args()
    NODE, PNPM = args.node.resolve(), args.pnpm.resolve()
    for binary in [NODE, PNPM]:
        if not binary.is_file() or not os.access(binary, os.X_OK):
            p.error(f'Executable not found: {binary}; set --node/--pnpm or NODE/PNPM')
    ENV = {**ENV, 'PATH': f'{NODE.parent}:{PNPM.parent}:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin'}
    if not re.fullmatch(r'(?:docker.io/)?grafana/grafana:13\.3\.0(?:-distroless)?(?:@sha256:[a-f0-9]{64})?', args.grafana_image):
        p.error('This preview uses only the official Grafana 13.3.0 image; custom images are not supported')
    if not re.fullmatch(r'(?:(?:docker.io/)?library/|docker.io/)?node:24\.22\.1-alpine(?:@sha256:[a-f0-9]{64})?', args.fixture_image):
        p.error('Use the official Node 24.22.1 Alpine fixture image')
    args.ports = [int(port) for port in args.ports.split(',')]
    args.lanes = args.lanes.split(',')
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', args.project) or len(args.ports) != 6 or len(set(args.ports)) != 6 or any(port < 1024 or port > 65535 for port in args.ports) or not args.lanes or set(args.lanes) - set(LANES):
        p.error('Use a lowercase project, six distinct ports above 1023, and lanes viewer,test-a,test-b')
    for key in ['output_dir', 'state_dir', 'log_dir', 'evidence_dir']:
        setattr(args, key, getattr(args, key).resolve())
    # Installation mode is part of workspace identity, so clean installs never see a cached symlink.
    args.output_dir = args.output_dir / args.dependencies
    if args.action == 'test':
        tests = sorted(str(path.relative_to(ROOT)) for folder in ['runtime', 'checks'] for path in (ROOT / folder).glob('*.test.mjs'))
        run([NODE, '--test', *tests])
        run(['python3', '-m', 'unittest', 'runtime/test_preview.py'])
        return
    if args.action == 'setup':
        if args.dependencies != 'frozen': p.error('setup requires --dependencies frozen')
        source, _ = workspace(args); print(source); return
    if args.action in ['status', 'down', 'smoke']:
        registry = args.output_dir / f'{args.project}-lanes.json'
        lanes = json.loads(registry.read_text())
        if args.action == 'smoke':
            args.evidence_dir.mkdir(parents=True, exist_ok=True)
            run([NODE, 'runtime/smoke.mjs', str(registry), str(args.evidence_dir)])
        else:
            for lane in args.lanes:
                check_ownership(lanes[lane]['project'])
                run(['docker', 'compose', '-p', lanes[lane]['project'], '-f', lanes[lane]['compose'], 'ps' if args.action == 'status' else 'down'])
        return
    if args.action == 'build':
        build(args); return
    replay_work = args.output_dir / 'replay-work'
    run([NODE, 'runtime/assemble.mjs', replay_work])
    replay_dir, _ = immutable(args.output_dir / 'replays', files_under(replay_work))
    if args.action == 'assemble': print(replay_dir); return
    # Check before starting or recreating anything. No global cleanup or image builds.
    available_ports(args)
    for image in [args.grafana_image, args.fixture_image]: run(['docker', 'image', 'inspect', image], capture=True)
    lock = build(args)
    window = json.loads((ROOT / 'runtime/queries.json').read_text())['window']
    from datetime import datetime, timezone
    dated = {key: datetime.fromtimestamp(value / 1000, timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z') for key, value in window.items()}
    native_dir = native_copies(args, dated)
    lanes = compose(args, lock, replay_dir, native_dir)
    for lane, item in lanes.items():
        run(['docker', 'compose', '-p', item['project'], '-f', item['compose'], 'up', '-d', '--no-build', '--pull', 'never'])
        wait_ready(item['fixture'] + '/health')
        wait_ready(item['url'].split(HOME)[0] + '/api/health')
    print(json.dumps(lanes, indent=2))


if __name__ == '__main__':
    main()
