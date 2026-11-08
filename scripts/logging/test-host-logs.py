#!/usr/bin/env python3
"""Run a host's production Fluent Bit config against this machine's journal and check what VictoriaLogs stores.

Writes a syslog line at two levels, a container's stdout and stderr through Docker's journald
driver, a kernel message, and native journal entries that repeat MESSAGE or hold a NUL byte, then
sends the journal through proxmox/monitoring/logs with only the endpoint swapped: a local
VictoriaLogs, reached with the query arguments pve-sbx-1's ingest route adds, and a client header
that tries to rename the host. Fluent Bit runs under the memory limit compose.yaml gives it. Also
checks that every host's log route stamps the identity its metrics route does and pins the tenant
headers. Needs Docker on a machine whose Docker daemon logs to systemd-journald, such as a GitHub
runner or Colima's VM; the images are pinned.

Then runs PVE's task log overlay, which includes that configuration, on a volume laid out like
/var/log/pve/tasks: a first start with old history and index files, a task written while it runs,
a restart with tasks written while it was stopped, a reused inode, an aged file that resumes and an
overlong line. Every stored record is compared with what should have arrived, once each.

Then runs the Sparks' inference overlay and sends request lines from each TensorFold engine through
Docker's fluentd driver, with the options and labels the SparkRun adapter sets, and checks the fields
each line is read into. `test-host-logs.py inference` runs this part alone; it needs Docker and yq.

Finally runs Athena's collector from synology/monitoring/compose.yaml, with its hardening and mounts,
on a journal systemd 219 writes, whose files only DSM's log group can read here: publication, a
first start at the end of the journal, a compressed record and a restart that resumes from the
cursor. `test-host-logs.py nas` runs this part alone; it needs only Docker.
"""
import ast
from collections import Counter
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOGS = ROOT / 'proxmox/monitoring/logs'
TASKS = ROOT / 'proxmox/monitoring/task-logs'
INFERENCE = ROOT / 'sparks/monitoring/inference-logs'
ADAPTER = ROOT / 'sparks/inference/sparkrun/plugins/tensorfold/__init__.py'
SYNOLOGY = ROOT / 'synology/monitoring'
VMUSERS = ROOT / 'kubernetes/apps/observability/metrics-ingest/app/vmusers.yaml'
ALPINE_IMAGE = 'alpine:3.22@sha256:2e0b81d60597f449baa56dc25d355367869d2dca4cd57e866f1e2b52af3eb55f'
# DSM 7.4 runs systemd 219, as this image's journald does.
JOURNALD_IMAGE = 'centos:7@sha256:be65f488b7764ad3638f236b7b515b3678369a5124c47b8d32916d6487418ea4'
# journald 219 exits unless PID 1 is in a v1 name=systemd cgroup, which a cgroup v2 host lacks.
JOURNALD = """
mkdir -p /run/systemd/journal
echo 1:name=systemd:/ > /tmp/cgroup
mount --bind /tmp/cgroup /proc/1/cgroup
exec /usr/lib/systemd/systemd-journald
"""
LOG_PATH = '/insert/opentelemetry/v1/logs'
# journald's native protocol: KEY=value lines, or KEY, a little-endian length and raw bytes.
NATIVE = r'''
import socket, struct, sys
run = sys.argv[1]
def entry(*fields):
    out = b''
    for key, value in fields:
        value = value.encode()
        out += key + (b'\n' + struct.pack('<Q', len(value)) + value if b'\0' in value else b'=' + value) + b'\n'
    socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM).sendto(out, '/run/systemd/journal/socket')
entry((b'SYSLOG_IDENTIFIER', 'host-logs-test'), (b'PRIORITY', '6'), (b'MESSAGE', run + ' first'), (b'MESSAGE', run + ' second'))
entry((b'SYSLOG_IDENTIFIER', 'host-logs-test'), (b'PRIORITY', '6'), (b'MESSAGE', run + ' before\0after'))
'''


# Long enough for a refresh to find the file and notice its age.
SCAN_WAIT = 12
FAILED_BACKUP = (b'INFO: starting new backup job\n'
                 b'INFO: Starting Backup of guest\n'
                 b'\n'
                 b'WARN: a storage is running low\n'
                 b'WARNING: a tool had a warning\n'
                 b'transferred 1.00 GiB of 2.00 GiB (50.00%)\n'
                 b'progress 10%\rprogress 20%\n'
                 b'trailing space   \n'
                 b'before\0after\n'
                 b'2026-10-30 12:00:00 ERROR: migration aborted\n'
                 b'an ERROR: elsewhere in a line\n'
                 b'ERROR: Backup of guest failed - no space left\n'
                 b'TASK ERROR: job errors\n')
SEVERITIES = {
    'INFO: starting new backup job': ('9', 'info'),
    'WARN: a storage is running low': ('13', 'warning'),
    'WARNING: a tool had a warning': ('13', 'warning'),
    'transferred 1.00 GiB of 2.00 GiB (50.00%)': ('0', None),
    '2026-10-30 12:00:00 ERROR: migration aborted': ('17', 'err'),
    'an ERROR: elsewhere in a line': ('0', None),
    'ERROR: Backup of guest failed - no space left': ('17', 'err'),
    'TASK ERROR: job errors': ('17', 'err'),
    'TASK WARNINGS: 1': ('13', 'warning'),
    'TASK OK': ('9', 'info'),
}


def pinned(path, pattern):
    return re.search(pattern, path.read_text()).group(1)


def log_routes():
    """Each host's log query arguments, checked against its metrics identity."""
    users = json.loads(subprocess.run(['yq', '-o=json', '-I=0', 'ea', '[.]', str(VMUSERS)],
                                      check=True, capture_output=True, text=True).stdout)
    routes, failures = {}, []
    for user in users:
        name = user['spec']['name']
        refs = user['spec']['targetRefs']
        labels = dict(v.split('=', 1) for r in refs for q in r.get('query_args', []) if q['name'] == 'extra_label' for v in q['values'])
        logs = [r for r in refs if LOG_PATH in r.get('paths', [])]
        if not logs:
            continue
        args = {q['name']: q['values'] for q in logs[0]['query_args']}
        want = {'extra_fields': [f"host.name={labels['instance']},service.namespace={labels['platform']}"],
                '_stream_fields': ['host.name,service.name']}
        if args != want:
            failures.append(f'{name}: log route query args {args} should be {want}')
        if logs[0].get('headers') != ['AccountID: 0', 'ProjectID: 0']:
            failures.append(f"{name}: log route should pin the tenant headers to 0, has {logs[0].get('headers')}")
        routes[name] = args
    return routes, failures


def test_config(query, path=LOGS / 'fluent-bit.yaml'):
    config = path.read_text()
    for old, new in (('host: metrics-ingest.home.kelch.io', 'host: vl'), ('port: 443', 'port: 9428'),
                     ('tls: on', 'tls: off'), ('tls.verify: on', 'tls.verify: off'),
                     (f'logs_uri: {LOG_PATH}', f'logs_uri: {LOG_PATH}?{query}'),
                     ('        - Authorization Bearer ${INGEST_TOKEN}\n',
                      '        - Authorization Bearer ${INGEST_TOKEN}\n        - VL-Extra-Fields host.name=spoofed,service.namespace=spoofed\n')):
        assert config.count(old) == 1, old
        config = config.replace(old, new)
    return config


def query(port, logsql):
    data = urllib.parse.urlencode({'query': logsql}).encode()
    with urllib.request.urlopen(f'http://127.0.0.1:{port}/select/logsql/query', data, timeout=10) as r:
        return [json.loads(line) for line in r.read().decode().splitlines() if line.strip()]


def docker(*args, **kwargs):
    return subprocess.run(['docker', *args], check=True, capture_output=True, text=True, **kwargs)


def collector_args(net, work, memory, config):
    """The collector as compose.yaml runs it, reading this machine's journal."""
    return ['--name', f'{net}-fb', '--network', net, '--memory', memory, '--user', '0:0', '--read-only', '--cap-drop', 'ALL',
            '--security-opt', 'no-new-privileges:true',
            '-v', '/var/log/journal:/var/log/journal:ro', '-v', '/run/log/journal:/run/log/journal:ro',
            '-v', '/etc/machine-id:/etc/machine-id:ro', '-v', f'{work}/token:/etc/monitoring:ro',
            '-v', f'{work}/fluent-bit.yaml:/fluent-bit/etc/fluent-bit.yaml:ro',
            '-v', f'{LOGS}/host.lua:/fluent-bit/etc/host.lua:ro', '-v', f'{net}-state:/state'], ['-c', f'/fluent-bit/etc/{config}']


def write_fixtures(run, python_image):
    docker('run', '--rm', '-v', '/dev/log:/dev/log', ALPINE_IMAGE, 'sh', '-c',
           f'logger -t host-logs-test -p daemon.warning "{run} syslog warning"; logger -t host-logs-test -p user.err "{run} syslog error"')
    docker('run', '--rm', '--log-driver', 'journald', '--name', f'host-logs-{run}', ALPINE_IMAGE, 'sh', '-c',
           f'echo "{run} container stdout"; echo "{run} container stderr" >&2')
    docker('run', '--rm', '--privileged', ALPINE_IMAGE, 'sh', '-c', f'echo "<4>host-logs-test: {run} kernel warning" > /dev/kmsg')
    docker('run', '--rm', '-v', '/run/systemd/journal/socket:/run/systemd/journal/socket', python_image, 'python', '-c', NATIVE, run)


def expectations(run):
    stream = lambda service: f'{{host.name="pve-sbx-1",service.name="{service}"}}'
    identity = {'host.name': 'pve-sbx-1', 'service.namespace': 'pve'}
    return {
        f'{run} syslog warning': {**identity, '_stream': stream('host-logs-test'), 'service.name': 'host-logs-test',
                                  'severity_number': '13', 'severity_text': 'warning', 'syslog.facility': 'daemon'},
        f'{run} syslog error': {**identity, 'service.name': 'host-logs-test', 'severity_number': '17',
                                'severity_text': 'err', 'syslog.facility': 'user'},
        f'{run} container stdout': {**identity, '_stream': stream(f'host-logs-{run}'), 'service.name': f'host-logs-{run}',
                                    'container.name': f'host-logs-{run}', 'container.image.name': ALPINE_IMAGE,
                                    'log.iostream': 'stdout', 'severity_number': '0'},
        f'{run} container stderr': {**identity, 'service.name': f'host-logs-{run}', 'log.iostream': 'stderr',
                                    'severity_number': '0'},
        # journald takes the kernel line's `host-logs-test:` prefix as its identifier.
        f'{run} kernel warning': {**identity, '_stream': stream('kernel'), 'service.name': 'kernel',
                                                  'severity_number': '13', 'severity_text': 'warning'},
        f'{run} first\n{run} second': {**identity, 'service.name': 'host-logs-test', 'severity_number': '9'},
        f'{run} before\\0after': {**identity, 'service.name': 'host-logs-test', 'severity_number': '9'},
    }


class Tasks:
    """Writes task files through a helper container, as root, the way Proxmox lays them out."""

    def __init__(self, helper):
        self.helper = helper
        self.pid = 0x1000
        self.sh('mkdir -p /tasks/0 /tasks/1 /tasks/2 /tasks/3 /tasks/4 /tasks/5 /tasks/6 /tasks/7 '
                '/tasks/8 /tasks/9 /tasks/A /tasks/B /tasks/C /tasks/D /tasks/E /tasks/F')

    def sh(self, script, data=None):
        return subprocess.run(['docker', 'exec', '-i', self.helper, 'sh', '-c', script], input=data,
                              check=True, capture_output=True).stdout.decode().strip()

    def path(self, kind, guest):
        self.pid += 1
        upid = f'UPID:pve-sbx-1:{self.pid:08X}:0ABC1234:{int(time.time()):08X}:{kind}:{guest}:root@pam:'
        return f'/tasks/{upid.split(":")[4][-1]}/{upid}'

    def create(self, kind, guest, data=b''):
        path = self.path(kind, guest)
        self.sh(f"install -m 0640 -o 33 -g 0 /dev/null '{path}'")
        self.append(path, data)
        return path

    def append(self, path, data):
        self.sh(f"cat >> '{path}'", data)

    def age(self, path):
        self.sh(f"touch -d '@{int(time.time()) - 3 * 86400}' '{path}'")


class Check:
    def __init__(self, port, tasks):
        self.port, self.tasks = port, tasks
        self.want = Counter()
        self.markers = 0

    def rows(self):
        return query(self.port, 'service.name:=pve-task')

    def expect(self, data):
        self.want.update(line.strip().replace('\0', '\\0') for line in data.decode().split('\n') if line.strip())

    def settle(self, step):
        """A new task is read after everything before it, so what its marker follows is final."""
        self.markers += 1
        marker = f'marker {self.markers}'
        self.tasks.create('marker', str(self.markers), f'{marker}\n'.encode())
        self.want[marker] = 1
        deadline = time.time() + 60
        got = Counter()
        while time.time() < deadline:
            try:
                got = Counter(r['_msg'] for r in self.rows())
            except OSError:
                pass
            if marker in got and got == self.want:
                return
            time.sleep(1)
        extra, missing = got - self.want, self.want - got
        raise SystemExit(f'{step}: unexpected {dict(extra)}, missing {dict(missing)}')


def output_parity(overlay=TASKS / 'pve-tasks.yaml'):
    """An overlay's output must stay the shared one, apart from what it matches."""
    def output(path):
        return json.loads(subprocess.run(['yq', '-o=json', '.pipeline.outputs[0] | del(.alias) | del(.match)', str(path)],
                                         check=True, capture_output=True, text=True).stdout)
    if output(LOGS / 'fluent-bit.yaml') != output(overlay):
        sys.exit(f'The output in {overlay.name} differs from the shared output in fluent-bit.yaml')


def scenario(tasks, check, collector):
    # First start: recently modified files, not the old task, index files or anything else there.
    tasks.age(tasks.create('vzdump', '101', b'too old to read\nTASK OK\n'))
    tasks.create('vzdump', '102', FAILED_BACKUP)
    check.expect(FAILED_BACKUP)
    tasks.sh("for f in index index.1 active .active.lock 0/notes.log; do "
             "echo 'UPID:pve-sbx-1:0000:0000:0000:vzdump:101:root@pam: 0 OK' > /tasks/$f; done")
    docker('start', collector)
    check.settle('first start')

    # A running task, including a line the worker has only half written.
    running = tasks.create('qmigrate', '103', b'first line\n')
    check.expect(b'first line\n')
    check.settle('new task')
    tasks.append(running, b'half a')
    check.settle('partial line held back')
    tasks.append(running, b' line\nTASK WARNINGS: 1\n')
    check.expect(b'half a line\nTASK WARNINGS: 1\n')
    check.settle('partial line completed')

    # A restart resumes each file from its position, and finds the tasks it missed. A worker
    # that died mid-line leaves an end that is never sent.
    active = tasks.create('qmclone', '104', b'before the restart\n')
    tasks.create('vzdump', '111', b'worker died after this\nin the middle of')
    check.expect(b'before the restart\nworker died after this\n')
    check.settle('before the restart')
    docker('stop', '-t', '40', collector)
    tasks.append(active, b'while stopped\nTASK OK\n')
    tasks.create('vzdump', '105', b'new while stopped\nTASK OK\n')
    tasks.age(tasks.create('vzdump', '106', b'too old by the restart\nTASK OK\n'))
    check.expect(b'while stopped\nTASK OK\nnew while stopped\nTASK OK\n')
    docker('start', collector)
    check.settle('after the restart')

    # An inode reused while the collector was stopped must not inherit the old file's position.
    # Renaming and emptying a file gives the state a filesystem reaches only when it chooses to.
    finished = tasks.create('vzdump', '107', b'deleted later\n' * 20)
    check.expect(b'deleted later\n' * 20)
    check.settle('task to delete')
    docker('stop', '-t', '40', collector)
    reused = tasks.path('vzdump', '108')
    tasks.sh(f"mv '{finished}' '{reused}'; : > '{reused}'")
    lines = b'reused inode, first line\n' + b'reused inode, later line\n' * 30 + b'TASK OK\n'
    tasks.append(reused, lines)
    check.expect(lines)
    docker('start', collector)
    check.settle('reused inode')

    # Identical contents must not hide a new UPID that reuses a finished task's inode.
    finished = tasks.create('qmstart', '112', b'TASK OK\n')
    check.expect(b'TASK OK\n')
    check.settle('identical task to delete')
    docker('stop', '-t', '40', collector)
    reused = tasks.path('qmstart', '113')
    tasks.sh(f"mv '{finished}' '{reused}'")
    check.expect(b'TASK OK\n')
    docker('start', collector)
    check.settle('same-content inode reuse')
    upid = reused.rsplit('/', 1)[-1]
    if [r['_msg'] for r in check.rows() if r.get('pve.task.upid') == upid] != ['TASK OK']:
        sys.exit('same-content inode reuse: new UPID was not stored exactly once')

    # A task can sit silent past ignore_older, and then continue from where it was.
    quiet = tasks.create('qmigrate', '109', b'before the silence\n')
    check.expect(b'before the silence\n')
    check.settle('before the silence')
    tasks.age(quiet)
    time.sleep(SCAN_WAIT)
    tasks.append(quiet, b'after the silence\nTASK OK\n')
    check.expect(b'after the silence\nTASK OK\n')
    check.settle('aged file resumed')

    # An overlong line is skipped, as on the cluster, and the task's status still arrives.
    tasks.create('vzdump', '110', b'x' * (2 * 1024 * 1024) + b'\nTASK ERROR: after a long line\n')
    check.expect(b'TASK ERROR: after a long line\n')
    check.settle('overlong line')


def check_fields(check):
    rows = check.rows()
    failures = []
    for row in rows:
        msg = row['_msg']
        wanted = {'host.name': 'pve-sbx-1', 'service.namespace': 'pve', 'service.name': 'pve-task',
                  '_stream': '{host.name="pve-sbx-1",service.name="pve-task"}'}
        if msg in SEVERITIES:
            wanted['severity_number'], text = SEVERITIES[msg]
            if text:
                wanted['severity_text'] = text
        for key, value in wanted.items():
            if row.get(key) != value:
                failures.append(f'{msg}: {key} should be {value!r}, got {row.get(key)!r}')
        for key in ('pipeline.normalize_error', 'pipeline.severity_unmapped'):
            if key in row:
                failures.append(f'{msg}: {key}={row[key]}')
    backup = next(r for r in rows if r['_msg'] == 'ERROR: Backup of guest failed - no space left')
    kind, guest = backup.get('pve.task.type'), backup.get('pve.task.id')
    if (kind, guest) != ('vzdump', '102') or not backup.get('pve.task.upid', '').endswith(':vzdump:102:root@pam:'):
        failures.append(f'task identity: {backup}')
    # The whole task can be read back by its UPID.
    upid = backup['pve.task.upid']
    same = [r for r in rows if r.get('pve.task.upid') == upid]
    if len(same) != len([l for l in FAILED_BACKUP.split(b'\n') if l]):
        failures.append(f'{upid}: {len(same)} records')
    return failures


def test_task_logs(routes, fb_image, vl_image, memory, base):
    output_parity()
    net = f'pvetasks-{uuid.uuid4().hex[:8]}'
    route = urllib.parse.urlencode(routes['pve-sbx-1'], doseq=True)
    with tempfile.TemporaryDirectory(dir=base) as tmp:
        work = Path(tmp)
        (work / 'token').mkdir()
        (work / 'token/token').write_text('test-token\n')
        (work / 'fluent-bit.yaml').write_text(test_config(route))
        (work / 'pve-tasks.yaml').write_text(test_config(route, TASKS / 'pve-tasks.yaml'))
        docker('network', 'create', net)
        try:
            docker('run', '-d', '--name', f'{net}-vl', '--network', net, '--network-alias', 'vl', '-p', '127.0.0.1::9428', vl_image)
            port = docker('port', f'{net}-vl', '9428/tcp').stdout.split(':')[-1].strip()
            docker('run', '-d', '--name', f'{net}-tasks', '-v', f'{net}-tasks:/tasks', ALPINE_IMAGE, 'sleep', '900')
            args, command = collector_args(net, work, memory, 'pve-tasks.yaml')
            docker('create', *args, '-v', f'{work}/pve-tasks.yaml:/fluent-bit/etc/pve-tasks.yaml:ro',
                   '-v', f'{TASKS}/pve-tasks.lua:/fluent-bit/etc/pve-tasks.lua:ro',
                   '-v', f'{net}-tasks:/var/log/pve/tasks:ro', fb_image, *command)
            tasks = Tasks(f'{net}-tasks')
            check = Check(port, tasks)
            try:
                scenario(tasks, check, f'{net}-fb')
                failures = check_fields(check)
                docker('run', '--rm', '-v', '/dev/log:/dev/log', ALPINE_IMAGE, 'logger', '-t', 'host-logs-test', f'{net} journal')
                deadline = time.time() + 30
                while not query(port, f'"{net} journal" service.name:=host-logs-test'):
                    if time.time() > deadline:
                        failures.append('the journal line never arrived through the overlay')
                        break
                    time.sleep(2)
            except SystemExit as error:
                failures = [str(error)]
            if failures:
                print(docker('logs', f'{net}-fb').stderr[-3000:], file=sys.stderr)
                sys.exit('\n'.join(failures))
        finally:
            subprocess.run(['docker', 'rm', '-f', f'{net}-fb', f'{net}-tasks', f'{net}-vl'], capture_output=True)
            subprocess.run(['docker', 'volume', 'rm', f'{net}-state', f'{net}-tasks'], capture_output=True)
            subprocess.run(['docker', 'network', 'rm', net], capture_output=True)
    print(f'PVE task logs: {sum(check.want.values())} records stored once each.')


# Lines as each engine prints them: the upstream Zig server, an older Zig fork without timings,
# TensorFold 0.6.0's Python server in Mia's GLM kit, and the Zig server's other request lines.
INFERENCE_LINES = {
    '[tensorfold] done chatcmpl-zig prompt=65 cached=0 thinking=False effort=none tokens=234 sha=7550f0ef0a8f finish=stop '
    'rounds=48 accepted=186/240 ttft=0.199 decode=4.082 elapsed=4.281 queued=0.250 tok/s=57.1 seed=20260930': {
        'event.name': 'inference.request.done', 'inference.request.id': 'chatcmpl-zig', 'inference.tokens.prompt': '65',
        'inference.tokens.cached': '0', 'inference.tokens.output': '234', 'inference.thinking': 'false',
        'inference.effort': 'none', 'inference.finish': 'stop', 'inference.spec.rounds': '48',
        'inference.spec.accepted': '186', 'inference.spec.drafted': '240', 'inference.ttft_s': '0.199',
        'inference.decode_s': '4.082', 'inference.total_s': '4.281', 'inference.queue_s': '0.25',
        'inference.output_tps': '57.1', 'inference.prefill_s': None},
    '[tensorfold] done chatcmpl-fork prompt=33 cached=0 thinking=False effort=none tokens=4 sha=bca3318b4669 finish=stop '
    'rounds=2 accepted=3/8': {
        'event.name': 'inference.request.done', 'inference.tokens.output': '4', 'inference.spec.drafted': '8',
        'inference.ttft_s': None, 'inference.total_s': None, 'inference.output_tps': None},
    '[tensorfold] done chatcmpl-py prompt=171612 cached=167864 thinking=True effort=high tokens=692 sha=0a1b2c3d4e5f '
    'finish=tool_calls tok/s=41.3 ttft=0.93s prefill=0.42s background preemptions=1 rounds=200 accepted=480/800 '
    'ms/round=21.5 forward=15.0 draft=4.0 post=2.5 rows=2.0 checkpoints=12 (3.10 GiB, hits=40 misses=3 evictions=0)': {
        'event.name': 'inference.request.done', 'inference.tokens.prompt': '171612', 'inference.tokens.cached': '167864',
        'inference.thinking': 'true', 'inference.finish': 'tool_calls', 'inference.output_tps': '41.3',
        'inference.ttft_s': '0.93', 'inference.prefill_s': '0.42', 'inference.preemptions': '1',
        'inference.decode_s': None, 'inference.queue_s': None},
    '[tensorfold] done chatcmpl-unmeasured prompt=10 cached=0 thinking=False effort=none tokens=0 sha=e3b0c44298fc '
    'finish=length tok/s=0.0 ttft=-1.00s prefill=-1.00s rounds=0 accepted=0/0 checkpoints=off': {
        'inference.finish': 'length', 'inference.ttft_s': None, 'inference.prefill_s': None},
    '[tensorfold] ended chatcmpl-err reason=error (TopKPastScratch) prompt=56 tokens=0 after=0.08s': {
        'event.name': 'inference.request.ended', 'inference.request.id': 'chatcmpl-err', 'inference.finish': 'error',
        'error.type': 'TopKPastScratch', 'inference.tokens.prompt': '56', 'inference.tokens.output': '0',
        'inference.total_s': '0.08'},
    '[tensorfold] ended chatcmpl-left reason=client-left prompt=59 tokens=1 after=0.41s': {
        'inference.finish': 'client-left', 'error.type': None, 'inference.total_s': '0.41'},
    '[tensorfold] refused cmpl-1: prompt is 2000000 tokens, over the 1048576-token window': {
        'event.name': 'inference.request.refused', 'inference.request.id': 'cmpl-1', 'inference.finish': 'refused'},
    '[tensorfold] 10.32.20.9 "POST /v1/chat/completions HTTP/1.1" 429 -': {
        'http.request.method': 'POST', 'url.path': '/v1/chat/completions', 'http.response.status_code': '429',
        'event.name': None},
    '[tensorfold] 127.0.0.1 "GET /metrics HTTP/1.1" 503 -': {'http.response.status_code': '503'},
    'info: prompt cache: 59 tokens, resumed at 0, kept 2; 2 states, 223 MiB and 0 MiB spare of 16384 MiB '
    '(hits 0, misses 1, evicted 0, refused 0, failed 0)': {'event.name': None, 'inference.request.id': None},
}
# From a one-Spark container, which carries no rank label.
SOLO_LINE = '[tensorfold] done chatcmpl-solo prompt=33 cached=0 thinking=False effort=none tokens=4 sha=bca3318b4669 finish=stop'
# Polling that succeeded, which the overlay drops.
INFERENCE_DROPPED = ['[tensorfold] 127.0.0.1 "GET /metrics HTTP/1.1" 200 -', '[tensorfold] 127.0.0.1 "GET /health HTTP/1.1" 200 -']


def adapter_docker_opts(port):
    """The log options the TensorFold adapter gives every rank's container, pointed at the test collector."""
    tree = ast.parse(ADAPTER.read_text())
    opts = next(ast.literal_eval(node.value) for node in tree.body
                if isinstance(node, ast.Assign) and [t.id for t in node.targets] == ['LOG_DOCKER_OPTS'])
    address = '--log-opt=fluentd-address=tcp://127.0.0.1:24224'
    if address not in opts:
        sys.exit(f'The adapter should send to the collector at {address}')
    return [f'--log-opt=fluentd-address=tcp://127.0.0.1:{port}' if o == address else o for o in opts]


# Accepts connections and never reads them, as a collector that has hung.
STALLED = """
import socket, time
server = socket.socket()
server.bind(('0.0.0.0', 24224))
server.listen()
held = []
while True:
    held.append(server.accept())
    print('accepted', flush=True)
"""


def test_stalled_collector(python_image, run):
    """A collector that stops reading must not stop Docker removing a rank's container."""
    name = f'inference-stalled-{run}'
    if not any(o.startswith('--log-opt=fluentd-write-timeout=') for o in adapter_docker_opts(24224)):
        sys.exit('The adapter should bound the log driver\'s writes with fluentd-write-timeout')
    try:
        docker('run', '-d', '--name', f'{name}-collector', '-p', '127.0.0.1::24224', python_image, 'python', '-c', STALLED)
        port = docker('port', f'{name}-collector', '24224/tcp').stdout.split(':')[-1].strip()
        # Far more than the socket buffers hold, so the driver's writes block.
        docker('run', '-d', '--name', f'{name}-rank0', *adapter_docker_opts(port), ALPINE_IMAGE,
               'sh', '-c', 'yes "$(printf "%0200d" 0)" | head -n 400000; sleep 600')
        # Once connected, the driver fills the socket buffers within a second and its writes block.
        deadline = time.time() + 30
        while 'accepted' not in docker('logs', f'{name}-collector').stdout:
            if time.time() > deadline:
                sys.exit('The log driver never connected to the stalled collector')
            time.sleep(1)
        time.sleep(5)
        started = time.time()
        try:
            subprocess.run(['docker', 'rm', '-f', f'{name}-rank0'], check=True, capture_output=True, timeout=60)
        except subprocess.TimeoutExpired:
            sys.exit('Removing a container hung while its log collector stopped reading')
        print(f'Stalled collector: container removed in {time.time() - started:.1f}s.')
    finally:
        subprocess.run(['docker', 'rm', '-f', f'{name}-rank0', f'{name}-collector'], capture_output=True)


def test_inference_logs(routes, fb_image, vl_image, memory, base):
    output_parity(INFERENCE / 'inference.yaml')
    run = uuid.uuid4().hex[:8]
    net = f'inference-{run}'
    route = urllib.parse.urlencode(routes['spark-1'], doseq=True)
    recipe = f'glm53-test-{run}'
    with tempfile.TemporaryDirectory(dir=base) as tmp:
        work = Path(tmp)
        (work / 'token').mkdir()
        (work / 'token/token').write_text('test-token\n')
        (work / 'fluent-bit.yaml').write_text(test_config(route))
        # Docker reaches the collector through a published port here, not the host's loopback.
        overlay = test_config(route, INFERENCE / 'inference.yaml')
        assert overlay.count('listen: 127.0.0.1') == 1
        (work / 'inference.yaml').write_text(overlay.replace('listen: 127.0.0.1', 'listen: 0.0.0.0'))
        docker('network', 'create', net)
        try:
            docker('run', '-d', '--name', f'{net}-vl', '--network', net, '--network-alias', 'vl', '-p', '127.0.0.1::9428', vl_image)
            port = docker('port', f'{net}-vl', '9428/tcp').stdout.split(':')[-1].strip()
            args, command = collector_args(net, work, memory, 'inference.yaml')
            docker('run', '-d', *args, '-p', '127.0.0.1::24224',
                   '-v', f'{work}/inference.yaml:/fluent-bit/etc/inference.yaml:ro',
                   '-v', f'{INFERENCE}/inference.lua:/fluent-bit/etc/inference.lua:ro', fb_image, *command)
            forward = docker('port', f'{net}-fb', '24224/tcp').stdout.split(':')[-1].strip()
            labels = {'sparkrun.recipe': f'/home/kelchm/sparkrun/recipes/glm53/{recipe}.yaml',
                      'sparkrun.served_model_name': 'GLM-5.3-Flash', 'sparkrun.runtime': 'tensorfold', 'sparkrun.rank': '0'}
            lines = [*INFERENCE_DROPPED, *INFERENCE_LINES]
            # Sent at once: the driver is asynchronous and holds lines until the collector answers.
            docker('run', '--rm', '-i', '--name', f'{net}-rank0', *adapter_docker_opts(forward),
                   *[a for k, v in labels.items() for a in ('--label', f'{k}={v}')], ALPINE_IMAGE,
                   'sh', '-c', 'cat; echo "stderr line" >&2', input='\n'.join(lines) + '\n')
            # SparkRun's one-Spark path labels no rank.
            docker('run', '--rm', '-i', '--name', f'{net}-solo', *adapter_docker_opts(forward),
                   *[a for k, v in labels.items() if k != 'sparkrun.rank' for a in ('--label', f'{k}={v}')],
                   ALPINE_IMAGE, 'cat', input=SOLO_LINE + '\n')
            rows = []
            deadline = time.time() + 60
            while time.time() < deadline:
                try:
                    rows = query(port, f'inference.recipe:={recipe}')
                except OSError:
                    rows = []
                if {'stderr line', SOLO_LINE} <= {r['_msg'] for r in rows}:
                    break
                time.sleep(2)
            failures = []
            counts = Counter(r['_msg'] for r in rows)
            want = {*INFERENCE_LINES, 'stderr line', SOLO_LINE}
            if set(counts) != want or set(counts.values()) != {1}:
                failures.append(f'stored {dict(counts)}, missing {want - set(counts)}')
            for row in rows:
                fields = {'host.name': 'spark-1', 'service.namespace': 'spark', 'service.name': 'inference',
                          '_stream': '{host.name="spark-1",service.name="inference"}', 'inference.model': 'GLM-5.3-Flash',
                          'inference.runtime': 'tensorfold', 'inference.rank': '0', 'container.name': f'{net}-rank0',
                          'log.iostream': 'stderr' if row['_msg'] == 'stderr line' else 'stdout', 'severity_number': '0',
                          'pipeline.normalize_error': None, **INFERENCE_LINES.get(row['_msg'], {})}
                if row['_msg'] == SOLO_LINE:
                    fields |= {'inference.rank': None, 'container.name': f'{net}-solo', 'event.name': 'inference.request.done',
                               'inference.tokens.output': '4'}
                for key, value in fields.items():
                    if row.get(key) != value:
                        failures.append(f"{row['_msg'][:50]}: {key} should be {value!r}, got {row.get(key)!r}")
            if failures:
                print(docker('logs', f'{net}-fb').stderr[-3000:], file=sys.stderr)
                sys.exit('\n'.join(failures))
        finally:
            subprocess.run(['docker', 'rm', '-f', f'{net}-fb', f'{net}-vl'], capture_output=True)
            subprocess.run(['docker', 'volume', 'rm', f'{net}-state'], capture_output=True)
            subprocess.run(['docker', 'network', 'rm', net], capture_output=True)
    print(f'Spark inference logs: {len(want)} lines stored once each, with their fields; polling dropped.')
    test_stalled_collector(pinned(ROOT / 'scripts/logging/test-fluent-bit.py', r"PYTHON_IMAGE = '([^']+)'"), run)


def binds(service, root):
    """A service's mounts, with each host path moved under root and repository files left in place."""
    args = []
    for volume in service['volumes']:
        source = volume['source']
        if not source.startswith(str(ROOT)):
            source = f'{root}{source}'
        args += ['-v', f"{source}:{volume['target']}" + (':ro' if volume.get('read_only') else '')]
    return args


def nas_collector_args(net, service, root):
    """The collector as compose.yaml runs it, on a test network instead of the host's."""
    if service['network_mode'] != 'host' or not service['read_only']:
        sys.exit('The NAS collector should be read-only on the host network')
    if service['depends_on']['publish']['condition'] != 'service_completed_successfully':
        sys.exit('The NAS collector should wait for publish to succeed')
    args = ['--name', f'{net}-fb', '--network', net, '--user', service['user'], '--memory', str(service['mem_limit']), '--read-only']
    for cap in service['cap_drop']:
        args += ['--cap-drop', cap]
    for option in service['security_opt']:
        args += ['--security-opt', option]
    return args + binds(service, root)


def wait_for(port, logsql, what, seconds=60):
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            rows = query(port, logsql)
            if rows:
                return rows
        except OSError:
            pass
        time.sleep(2)
    sys.exit(f'{what} never arrived')


def nas_scenario(run, net, port, write, service, root):
    """First start, mapping, then a restart. Returns the messages that should each be stored once."""
    write(f'{run} before the first start')
    docker('run', '-d', *nas_collector_args(net, service, root), service['image'], *service['command'])
    deadline = time.time() + 90
    while True:
        write(f'{run} ready')
        time.sleep(3)
        if query(port, f'"{run} ready"'):
            break
        if time.time() > deadline:
            sys.exit('The NAS collector delivered nothing')
    compressed = f'{run} compressed ' + 'x ' * 3000
    write(f'{run} warning', 'daemon.warning')
    write(f'{run} error', 'user.err')
    docker('exec', '-i', f'{net}-journald', 'systemd-cat', '-t', 'nas-logs-test', '-p', 'err', input=compressed + '\n')
    wait_for(port, f'"{run} compressed"', 'The compressed record')
    docker('stop', '-t', '40', f'{net}-fb')
    write(f'{run} while stopped')
    docker('start', f'{net}-fb')
    write(f'{run} after the restart')
    wait_for(port, f'"{run} after the restart"', 'The line after the restart')
    wait_for(port, f'"{run} while stopped"', 'The line written while stopped')
    return {f'{run} warning': ('13', 'warning', 'daemon'), f'{run} error': ('17', 'err', 'user'), compressed.rstrip(): ('17', 'err', None),
            f'{run} while stopped': ('9', 'info', 'user'), f'{run} after the restart': ('9', 'info', 'user')}


def check_nas_rows(run, port, want):
    failures = []
    rows = [r for r in query(port, f'"{run}"') if r['_msg'] != f'{run} ready']
    counts = Counter(r['_msg'] for r in rows)
    if set(counts) != set(want) or set(counts.values()) != {1}:
        failures.append(f'stored {dict(Counter({m[:60]: c for m, c in counts.items()}))}')
    for row in rows:
        fields = {'host.name': 'nas', 'service.namespace': 'synology', 'service.name': 'nas-logs-test',
                  '_stream': '{host.name="nas",service.name="nas-logs-test"}'}
        if row['_msg'] in want:
            severity, text, facility = want[row['_msg']]
            fields |= {'severity_number': severity, 'severity_text': text}
            fields |= {'syslog.facility': facility} if facility else {}
        for key, value in fields.items():
            if row.get(key) != value:
                failures.append(f"{row['_msg'][:60]}: {key} should be {value!r}, got {row.get(key)!r}")
        for key in ('pipeline.normalize_error', 'pipeline.severity_unmapped'):
            if key in row:
                failures.append(f"{row['_msg'][:60]}: {key}={row[key]}")
    return failures


def test_nas(routes, vl_image):
    services = json.loads(subprocess.run(['docker', 'compose', '-f', str(SYNOLOGY / 'compose.yaml'), 'config', '--format', 'json'],
                                         check=True, capture_output=True, text=True).stdout)['services']
    publish, collector = services['publish'], services['fluent-bit']
    run = uuid.uuid4().hex[:8]
    net = f'naslogs-{run}'
    # On the Docker host's own disk, so ownership is as on Athena whatever shares a client's files.
    root = f'/var/tmp/{net}'
    config = f'{root}/volume1/docker/monitoring/config'
    machine = uuid.uuid4().hex

    def host(script, data=None):
        return docker('run', '--rm', '-i', '-v', '/var/tmp:/var/tmp', ALPINE_IMAGE, 'sh', '-ec', script, input=data).stdout

    def write(message, priority='user.info'):
        docker('exec', f'{net}-journald', 'logger', '-t', 'nas-logs-test', '-p', priority, message)

    docker('run', '--rm', '--network', 'none', '-v', f'{SYNOLOGY}/scrape.yaml:/scrape.yaml:ro', services['vmagent']['image'],
           '-promscrape.config=/scrape.yaml', '-promscrape.config.dryRun')
    try:
        host(f"""
            mkdir -p {root}/etc {config} {root}/run/log {root}/var/log
            install -d -m 0700 {root}/volume1/docker/monitoring/logs
            install -d -o 0 -g 19 -m 0755 {root}/run/log/journal
            install -d -o 0 -g 19 -m 2755 {root}/var/log/journal
            echo {machine} > {root}/etc/machine-id
            install -m 0600 /dev/stdin {root}/volume1/docker/monitoring/token
        """, 'test-token\n')
        docker('run', '--rm', *binds(publish, root), publish['image'], *publish['command'])
        for name, source in (('scrape.yaml', SYNOLOGY / 'scrape.yaml'), ('snmp.yml', SYNOLOGY / 'snmp.yml'),
                             ('fluent-bit.yaml', SYNOLOGY / 'logs/fluent-bit.yaml'), ('host.lua', SYNOLOGY / 'logs/host.lua')):
            published = host(f'sha256sum {config}/{name}').split()[0]
            if published != hashlib.sha256(source.read_bytes()).hexdigest():
                sys.exit(f'publish did not copy {name}')
        host(f'cat > {config}/fluent-bit.yaml', test_config(urllib.parse.urlencode(routes['nas'], doseq=True), SYNOLOGY / 'logs/fluent-bit.yaml'))

        docker('run', '-d', '--name', f'{net}-journald', '--cap-add', 'SYS_ADMIN', '--security-opt', 'apparmor=unconfined',
               '-v', f'{root}/etc/machine-id:/etc/machine-id:ro', '-v', f'{root}/run/log/journal:/run/log/journal',
               JOURNALD_IMAGE, 'sh', '-ec', JOURNALD)
        journal = f'{root}/run/log/journal/{machine}/system.journal'
        host(f'for i in $(seq 50); do [ -f {journal} ] && break; sleep 0.2; done; chown 65534:19 {journal}; chmod 0640 {journal}')
        header = docker('exec', f'{net}-journald', 'journalctl', '-D', f'/run/log/journal/{machine}', '--header').stdout
        if 'COMPRESSED-XZ' not in header:
            sys.exit(f'The fixture is not a compressed journal:\n{header}')

        # Without capabilities, the journal opens only through the group compose.yaml gives.
        magic = [subprocess.run(['docker', 'run', '--rm', '--user', user, '--cap-drop', 'ALL', '-v', f'{journal}:/journal:ro',
                                 ALPINE_IMAGE, 'head', '-c', '8', '/journal'], capture_output=True, text=True).stdout
                 for user in (collector['user'], '0:0')]
        if magic != ['LPKSHHRH', '']:
            sys.exit(f"Group access to the journal should decide this test: read {magic} as {collector['user']} and 0:0")

        docker('network', 'create', net)
        docker('run', '-d', '--name', f'{net}-vl', '--network', net, '--network-alias', 'vl', '-p', '127.0.0.1::9428', vl_image)
        port = docker('port', f'{net}-vl', '9428/tcp').stdout.split(':')[-1].strip()
        time.sleep(2)
        try:
            want = nas_scenario(run, net, port, write, collector, root)
            failures = check_nas_rows(run, port, want)
            state = host(f'ls {root}/volume1/docker/monitoring/logs').split()
            if 'journal.db' not in state or 'storage' not in state:
                failures.append(f'state volume holds {state}')
        except SystemExit as error:
            failures = [str(error)]
            want = {}
        if failures:
            print(docker('logs', f'{net}-fb').stderr[-3000:], file=sys.stderr)
            sys.exit('\n'.join(failures))
    finally:
        subprocess.run(['docker', 'rm', '-f', f'{net}-fb', f'{net}-vl', f'{net}-journald'], capture_output=True)
        subprocess.run(['docker', 'network', 'rm', net], capture_output=True)
        subprocess.run(['docker', 'run', '--rm', '-v', '/var/tmp:/var/tmp', ALPINE_IMAGE, 'rm', '-rf', root], capture_output=True)
    print(f'NAS journal: {len(want)} records from a systemd 219 journal stored once each.')


def main():
    routes, failures = log_routes()
    if 'pve-sbx-1' not in routes:
        failures.append('pve-sbx-1 has no log route')
    if failures:
        sys.exit('\n'.join(failures))
    fb_image = pinned(ROOT / 'proxmox/monitoring/compose.yaml', r'image: (\S*fluent/fluent-bit:\S+)')
    vl_image = pinned(ROOT / 'scripts/logging/test-logsql-rules.py', r"VL_IMAGE = '([^']+)'")
    python_image = pinned(ROOT / 'scripts/logging/test-fluent-bit.py', r"PYTHON_IMAGE = '([^']+)'")
    memory = pinned(ROOT / 'proxmox/monitoring/compose.yaml', r'/fluent-bit/etc/\S+"\]\n(?:.*\n)*?    mem_limit: (\S+)')
    run = uuid.uuid4().hex[:8]
    net = f'hostlogs-{run}'
    base = Path(os.environ.get('FLUENT_BIT_TEST_TMP', Path.home() / '.cache'))
    base.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=base) as tmp:
        work = Path(tmp)
        (work / 'token').mkdir()
        (work / 'token/token').write_text('test-token\n')
        (work / 'fluent-bit.yaml').write_text(test_config(urllib.parse.urlencode(routes['pve-sbx-1'], doseq=True)))
        docker('network', 'create', net)
        try:
            docker('run', '-d', '--name', f'{net}-vl', '--network', net, '--network-alias', 'vl', '-p', '127.0.0.1::9428', vl_image)
            port = docker('port', f'{net}-vl', '9428/tcp').stdout.split(':')[-1].strip()
            args, command = collector_args(net, work, memory, 'fluent-bit.yaml')
            docker('run', '-d', *args, fb_image, *command)
            # The input starts at the end of the journal, so wait until a line written now arrives.
            deadline = time.time() + 90
            while time.time() < deadline:
                docker('run', '--rm', '-v', '/dev/log:/dev/log', ALPINE_IMAGE, 'logger', '-t', 'host-logs-test', f'{run} ready')
                time.sleep(3)
                try:
                    if query(port, f'"{run} ready"'):
                        break
                except OSError:
                    pass
            else:
                sys.exit('Fluent Bit delivered nothing:\n' + docker('logs', f'{net}-fb').stderr[-3000:])
            write_fixtures(run, python_image)
            want = expectations(run)
            rows = []
            deadline = time.time() + 60
            while time.time() < deadline:
                rows = query(port, f'"{run}" -"{run} ready"')
                if want.keys() <= {r['_msg'] for r in rows}:
                    break
                time.sleep(2)
            for row in query(port, f'"{run}"'):
                for key in ('pipeline.normalize_error', 'pipeline.severity_unmapped'):
                    if key in row:
                        failures.append(f'{row["_msg"]}: {key}={row[key]}')
                if row.get('host.name') != 'pve-sbx-1' or row.get('service.namespace') != 'pve':
                    failures.append(f'{row["_msg"]}: identity {row.get("host.name")}/{row.get("service.namespace")}')
            by_msg = {r['_msg']: r for r in rows}
            for msg, fields in want.items():
                row = by_msg.get(msg)
                if row is None:
                    failures.append(f'missing: {msg}')
                    continue
                for key, value in fields.items():
                    if row.get(key) != value:
                        failures.append(f'{msg}: {key} should be {value!r}, got {row.get(key)!r}')
                if not msg.startswith(f'{run} container'):
                    for key in ('container.name', 'log.iostream'):
                        if key in row:
                            failures.append(f'{msg}: {key} should be absent')
            if failures:
                print(docker('logs', f'{net}-fb').stderr[-3000:], file=sys.stderr)
                print(json.dumps(rows, indent=1), file=sys.stderr)
                sys.exit('\n'.join(failures))
        finally:
            subprocess.run(['docker', 'rm', '-f', f'{net}-fb', f'{net}-vl'], capture_output=True)
            subprocess.run(['docker', 'volume', 'rm', f'{net}-state'], capture_output=True)
            subprocess.run(['docker', 'network', 'rm', net], capture_output=True)
    print(f'Host logs: {len(routes)} log routes and {len(want)} journal records passed.')
    test_task_logs(routes, fb_image, vl_image, memory, base)
    test_inference_logs(routes, fb_image, vl_image, memory, base)
    test_nas(routes, vl_image)


def images():
    return (pinned(ROOT / 'proxmox/monitoring/compose.yaml', r'image: (\S*fluent/fluent-bit:\S+)'),
            pinned(ROOT / 'scripts/logging/test-logsql-rules.py', r"VL_IMAGE = '([^']+)'"),
            pinned(ROOT / 'proxmox/monitoring/compose.yaml', r'/fluent-bit/etc/\S+"\]\n(?:.*\n)*?    mem_limit: (\S+)'))


if __name__ == '__main__':
    if sys.argv[1:] == ['nas']:
        test_nas(log_routes()[0], pinned(ROOT / 'scripts/logging/test-logsql-rules.py', r"VL_IMAGE = '([^']+)'"))
    elif sys.argv[1:] == ['inference']:
        fb_image, vl_image, memory = images()
        base = Path(os.environ.get('FLUENT_BIT_TEST_TMP', Path.home() / '.cache'))
        base.mkdir(parents=True, exist_ok=True)
        test_inference_logs(log_routes()[0], fb_image, vl_image, memory, base)
    else:
        main()
