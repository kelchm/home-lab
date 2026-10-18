#!/usr/bin/env python3
"""Qualify UniFi syslog relay semantics using synthetic loopback traffic only.

Adapted from the isolated reproduce-workaround.py experiment. Requires official
v1.53.0 victoria-logs-prod and vlagent-prod binaries; no cluster access, external
HTTP targets, notifications, forced flushes, or merges. Results contain synthetic
fixtures only. A nonzero exit means a qualification assertion failed.
"""

import argparse
import ast
from collections import Counter
import datetime as dt
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import random
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request


ROOT = Path(__file__).resolve().parents[2]
PARENT = Path('/Users/kelchm/.t3/artifacts/log-query-ux-c76948f4')
STREAM_FIELDS = 'hostname,app_name,proc_id,cef.device_vendor,cef.device_product,cef.device_event_class_id'
HEADERS = 'VL-Msg-Field:cef.extension.msg,_msg,cef.name^^VL-Stream-Fields:' + STREAM_FIELDS
FIXTURE_ARGS = [
    '-syslog.useLocalTimestamp.udp=true', '-syslog.useRemoteIP.udp=true',
    '-syslog.timezone=America/New_York', '-defaultMsgValue=',
    '-remoteWrite.format=jsonline', "-remoteWrite.headers='" + HEADERS + "'",
    '-remoteWrite.maxDiskUsagePerURL=1GiB',
]


def http(base, path, data=None, headers=None):
    # Every HTTP request, including proxy forwarding, must remain loopback-only.
    assert urllib.parse.urlparse(base).hostname == '127.0.0.1', base
    if isinstance(data, dict):
        data = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(base + path, data=data, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.read().decode()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f'{path}: HTTP {exc.code}: {exc.read().decode()}') from exc


def port(kind=socket.SOCK_STREAM):
    with socket.socket(socket.AF_INET, kind) as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def until(fn, description, timeout=30):
    deadline = time.monotonic() + timeout
    value = None
    while time.monotonic() < deadline:
        value = fn()
        if value:
            return value
        time.sleep(.1)
    raise AssertionError(f'Timed out: {description}; last result={value!r}')


def query(base, expression='*', limit=10000):
    return [json.loads(line) for line in http(base, '/select/logsql/query',
            {'query': expression, 'limit': str(limit)}).splitlines() if line]


def metrics(base):
    text = http(base, '/metrics')
    return {line.rsplit(' ', 1)[0]: float(line.rsplit(' ', 1)[1])
            for line in text.splitlines() if line and not line.startswith('#')
            and re.search(r'(persistentqueue|remotewrite|rows_ingested|syslog|dropped|drop_)', line)}


def metric_sum(snapshot, fragment):
    return sum(value for key, value in snapshot.items() if fragment in key)


def sorted_rows(rows):
    return sorted(rows, key=lambda row: json.dumps(row, sort_keys=True))


def send(udp_port, messages, delay=.003):
    timestamps = {}
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        for host, message in messages:
            timestamps[host] = time.time()
            sock.sendto(message.encode(), ('127.0.0.1', udp_port))
            time.sleep(delay)
    return timestamps


class Process:
    def __init__(self, binary, args, log):
        self.log = log
        self.output = log.open('a')
        self.process = subprocess.Popen([str(binary), *args], stdout=self.output, stderr=subprocess.STDOUT)

    def ready(self, base):
        def check():
            if self.process.poll() is not None:
                raise RuntimeError(f'Process exited {self.process.returncode}: {self.log.read_text()[-5000:]}')
            try:
                return http(base, '/health') == 'OK'
            except (OSError, RuntimeError):
                return False
        until(check, 'process health', 15)

    def stop(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
                raise AssertionError('SIGTERM failed to stop process gracefully')
        self.output.close()
        return self.process.returncode


class Proxy:
    """Controllable HTTP 503 endpoint; forwards only to the scratch backend."""
    def __init__(self, backend):
        self.backend = backend
        self.available = True
        self.failures = 0
        self.successes = 0
        proxy = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
                if not proxy.available:
                    proxy.failures += 1
                    self.send_response(503)
                    self.end_headers()
                    self.wfile.write(b'synthetic qualification outage')
                    return
                assert self.path == '/insert/jsonline', self.path
                headers = {key: value for key, value in self.headers.items()
                           if key.lower() not in ('host', 'content-length', 'connection')}
                try:
                    response = http(proxy.backend, self.path, body, headers).encode()
                except Exception as exc:
                    self.send_response(502)
                    self.end_headers()
                    self.wfile.write(str(exc).encode())
                    return
                proxy.successes += 1
                self.send_response(200)
                self.end_headers()
                self.wfile.write(response)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def fixtures():
    cases = []

    def cef(host, class_id, name, extension='', *, product='UniFi Network', proc='-'):
        message = f'CEF:0|Ubiquiti|{product}|10.6.106|{class_id}|{name}|5|{extension}'
        cases.append((host, f'<134>1 2020-01-02T03:04:05Z {host} CEF {proc} - - {message}'))

    cef('wan-down', '100', 'Internet Down', 'msg=Primary WAN disconnected UNIFIwanName=WAN1')
    cef('wan-restored', '107', 'Internet Restored', 'msg=Internet restored after temporary disconnection UNIFIwanName=WAN1')
    cef('wan-restored-two', '107', 'Internet Restored', 'msg=Recovered after temporary disconnection UNIFIwanName=WAN1')
    cef('wan-failover', '102', 'WAN Failover', 'msg=Secondary WAN active UNIFIwanName=WAN2')
    cef('wan-failover-no-msg-one', '102', 'WAN Failover', 'UNIFIwanName=WAN2')
    cef('wan-failover-no-msg-two', '102', 'WAN Failover', 'UNIFIwanName=WAN2')
    cef('ids', '200', 'Threat Detected', 'msg=ET TEST signature detected src=192.0.2.10 dst=8.8.8.8 UNIFIipsSignature=ET TEST')
    cef('ids-blocked', '201', 'Threat Detected and Blocked', 'msg=Blocked synthetic threat src=192.0.2.11 dst=1.1.1.1 UNIFIipsSignature=ET BLOCK')
    cef('honeypot', '202', 'Honeypot Triggered', 'msg=Synthetic honeypot contact src=192.0.2.16')
    cef('honeypot-no-msg', '202', 'Honeypot Triggered', 'src=192.0.2.17')
    cef('client', '400', 'Client Connected', 'msg=Synthetic client connected src=192.0.2.12 UNIFIclientAlias=fixture-client')
    cef('device', '512', 'Device Offline', 'msg=Synthetic switch offline UNIFIdeviceName=fixture-switch')
    cef('admin', '544', 'Administrator Login', 'msg=Synthetic admin logged in UNIFIadmin=fixture-admin')
    cef('unifi-os', '1', 'Console Updated', 'msg=Synthetic console update UNIFIconsoleName=fixture-console', product='UniFi OS')
    cef('unknown', '999999', 'Unrecognized Future Event', 'msg=Unknown class remains visible customField=keep-me', proc='73')
    cef('missing-msg', '777', 'Name fallback', 'src=192.0.2.13 customField=retained')
    cef('escaped', '778', 'Escaped \\| name', 'msg=Escaped equals\\=value and slash\\\\value customField=retained')
    cases += [
        ('malformed', '<134>1 2020-01-02T03:04:05Z malformed CEF - - - CEF:0|Ubiquiti|truncated'),
        ('dnsmasq', '<30>Jan  2 03:04:05 dnsmasq dnsmasq[42]: DHCPACK(br0) 192.0.2.50 02:00:00:00:00:01 fixture-client'),
        ('firewall', '<4>Jan  2 03:04:05 firewall kernel: [LAN_LOCAL-default-D] IN=br0 OUT= SRC=192.0.2.15 DST=192.0.2.1 PROTO=TCP DPT=443'),
        ('rfc5424-proc', '<30>1 2020-01-02T03:04:05Z rfc5424-proc dnsmasq 31415 fixture-id - query[A] fixture.example from 192.0.2.10'),
    ]
    return cases


def manifest_args(path):
    """Read literal YAML args list without a YAML dependency; fail on ambiguity."""
    if path is None:
        return None
    assert path.is_file(), f'Relay manifest does not exist: {path}'
    args = []
    for line in path.read_text().splitlines():
        match = re.match(r'^\s*-\s+((?:[\'\"])?-[-\w.]+.*)$', line)
        if not match:
            continue
        value = match.group(1).strip()
        if value[0] in ('"', "'"):
            # YAML single quotes escape themselves by doubling, unlike Python.
            value = value[1:-1].replace("''", "'") if value[0] == "'" else ast.literal_eval(value)
        else:
            value = value.split(' #', 1)[0].rstrip()
        args.append(value)
    assert args, f'No literal argument list found in {path}'
    return args


def option_map(args):
    options = {}
    for item in args:
        key = item.lstrip('-').split('=', 1)[0]
        assert key not in options, f'Duplicate manifest flag requires explicit qualification support: {key}'
        options[key] = item.split('=', 1)[1] if '=' in item else 'true'
    return options


def files_snapshot(path):
    return {str(p.relative_to(path)): p.stat().st_size for p in path.rglob('*') if p.is_file()}


def check_version(version):
    # Release binaries include their build timestamp and git-describe suffix.
    # Accept the exact release tag with zero commits after it, never an RC.
    assert re.fullmatch(r'(?:victoria-logs|vlagent)-\d{8}-\d{6}-tags-v1\.53\.0-0-g[0-9a-f]+', version), version


def qualify(args, result):
    result['binaries'] = {}
    for name in ('victoria-logs-prod', 'vlagent-prod'):
        binary = args.bin_dir / name
        version = subprocess.check_output([str(binary), '-version'], stderr=subprocess.STDOUT, text=True).strip()
        check_version(version)
        result['binaries'][name] = {'version': version, 'sha256': hashlib.sha256(binary.read_bytes()).hexdigest()}
    result['arguments'] = FIXTURE_ARGS
    deployed_args = manifest_args(args.manifest)
    result['manifest'] = {'path': str(args.manifest) if args.manifest else None, 'found': deployed_args is not None}
    if deployed_args is not None:
        deployed = option_map(deployed_args)
        for key, value in option_map(FIXTURE_ARGS).items():
            assert key in deployed, f'Missing required manifest flag: {key}'
            expected = value.strip("'\"")
            actual = deployed[key].strip("'\"")
            if key == 'remoteWrite.maxDiskUsagePerURL':
                expected = expected.replace('1GiB', '1073741824')
                actual = actual.replace('1GiB', '1073741824')
            assert actual == expected, (key, value, deployed.get(key))
        assert 'syslog.listenAddr.udp' in deployed, deployed
        assert deployed['remoteWrite.url'].endswith('/insert/jsonline'), deployed
        result['manifest']['checked_arguments'] = deployed_args
    processes = []
    proxy = None
    with tempfile.TemporaryDirectory(prefix='unifi-relay-qualification-') as directory:
        scratch = Path(directory)
        backend_port, baseline_port, relay_http = port(), port(), port()
        baseline_udp, relay_udp = port(socket.SOCK_DGRAM), port(socket.SOCK_DGRAM)
        backend = f'http://127.0.0.1:{backend_port}'
        baseline = f'http://127.0.0.1:{baseline_port}'
        relay_url = f'http://127.0.0.1:{relay_http}'
        try:
            for name, address, udp in [('backend', backend_port, None), ('baseline', baseline_port, baseline_udp)]:
                flags = [f'-httpListenAddr=127.0.0.1:{address}', f'-storageDataPath={scratch / name}', '-retentionPeriod=1d']
                if udp:
                    flags += [f'-syslog.listenAddr.udp=127.0.0.1:{udp}', '-syslog.useLocalTimestamp.udp=true',
                              '-syslog.useRemoteIP.udp=true', '-syslog.timezone=America/New_York']
                process = Process(args.bin_dir / 'victoria-logs-prod', flags, scratch / f'{name}.log')
                processes.append(process)
                process.ready(f'http://127.0.0.1:{address}')
            proxy = Proxy(backend)
            replacements = {'httpListenAddr': f'127.0.0.1:{relay_http}',
                            'syslog.listenAddr.udp': f'127.0.0.1:{relay_udp}',
                            'remoteWrite.url': f'{proxy.url}/insert/jsonline',
                            'tmpDataPath': str(scratch / 'queue'),
                            'remoteWrite.tmpDataPath': str(scratch / 'queue' / 'remote')}
            relay_options = option_map(deployed_args or [*FIXTURE_ARGS, '-remoteWrite.queues=2'])
            relay_options.update(replacements)
            assert not any(key.startswith('syslog.listenAddr.') and key != 'syslog.listenAddr.udp'
                           for key in relay_options), 'Only the remapped loopback UDP listener is allowed'
            assert all(key not in relay_options for key in ('envflag.enable', 'config'))
            relay_flags = [f'-{key}={value}' for key, value in relay_options.items()]
            result['runtime_relay_arguments'] = [flag.replace(str(scratch), '<scratch>') for flag in relay_flags]

            def start_relay():
                relay = Process(args.bin_dir / 'vlagent-prod', relay_flags, scratch / 'relay.log')
                processes.append(relay)
                relay.ready(relay_url)
                return relay

            relay = start_relay()
            cases = fixtures()
            send(baseline_udp, cases)
            sent = send(relay_udp, cases)
            visibility_start = time.monotonic()

            def complete(base):
                rows = query(base)
                return rows if len(rows) == len(cases) else None

            rows = until(lambda: complete(backend), 'relay fixtures visible without forced flush')
            result['query_visibility_seconds'] = round(time.monotonic() - visibility_start, 3)
            baseline_rows = until(lambda: complete(baseline), 'direct syslog baseline visible')
            by_host = {row['hostname']: row for row in rows}
            direct = {row['hostname']: row for row in baseline_rows}
            result['semantics'] = {'raw_rows': rows, 'baseline_rows': baseline_rows}
            assert len(by_host) == len(cases), ('duplicate fixture', rows)
            checks = []
            for host, _wire in cases:
                row, original = by_host[host], direct[host]
                assert row['remote_ip'] == '127.0.0.1', row
                arrival = dt.datetime.fromisoformat(row['_time'].replace('Z', '+00:00')).timestamp()
                assert abs(arrival - sent[host]) < 3, (host, arrival, sent[host])
                assert row['timestamp'] == original['timestamp'], (host, row, original)
                if '2020-' in _wire:
                    assert row['timestamp'] == '2020-01-02T03:04:05Z', row
                else:
                    assert row['timestamp'].endswith('-01-02T08:04:05Z'), row
                assert row['_stream'] == original['_stream'], (host, row['_stream'], original['_stream'])
                expected_msg = original.get('cef.extension.msg') or original.get('_msg') or original.get('cef.name')
                # Direct CEF ingestion uses a configured placeholder for absent _msg.
                if original.get('cef.name') and not original.get('cef.extension.msg'):
                    expected_msg = original['cef.name']
                assert row.get('_msg') == expected_msg, (host, row, expected_msg)
                assert 'cef.extension.msg' not in row, row
                for key, value in original.items():
                    if key not in ('_msg', '_time', 'cef.extension.msg'):
                        if key == 'cef.name' and not original.get('cef.extension.msg'):
                            assert key not in row, (host, 'fallback field is renamed', row)
                            continue
                        assert row.get(key) == value, (host, key, row.get(key), value)
                checks.append({'host': host, 'message': row['_msg'], 'stream': row['_stream'],
                               'timestamp': row['timestamp'], 'class': row.get('cef.device_event_class_id'),
                               'proc_id': row.get('proc_id')})
            assert by_host['rfc5424-proc']['proc_id'] == '31415', by_host['rfc5424-proc']
            assert by_host['dnsmasq']['proc_id'] == '42', by_host['dnsmasq']
            assert by_host['unknown']['cef.extension.customField'] == 'keep-me', by_host['unknown']
            assert 'CEF:0|Ubiquiti|truncated' in by_host['malformed']['_msg'], by_host['malformed']
            result['semantics'].update(passed=len(checks), fixtures=checks,
                                       fallback_semantics='Selecting cef.name as fallback renames it to _msg; cef.name is absent only on that fallback path.')

            invalid_copies = ['* | copy if (cef.extension.msg:*) cef.extension.msg as _msg',
                              '* | copy if cef.extension.msg:* cef.extension.msg as _msg']
            copy_errors = {}
            for invalid_copy in invalid_copies:
                try:
                    query(backend, invalid_copy)
                except RuntimeError as exc:
                    copy_error = str(exc)
                    assert 'HTTP 400' in copy_error and 'cannot parse "copy"' in copy_error, copy_error
                    copy_errors[invalid_copy] = copy_error
                else:
                    raise AssertionError('Conditional copy unexpectedly accepted; requalify query semantics')
            conditional = '* | format if (cef.extension.msg:*) "<cef.extension.msg>" as _msg'
            for name, base in [('old', baseline), ('new', backend)]:
                formatted = {row['hostname']: row for row in query(base, conditional)}
                for host, _ in cases:
                    expected = (direct[host].get('cef.extension.msg') or direct[host].get('_msg')) if name == 'old' else by_host[host]['_msg']
                    assert formatted[host].get('_msg') == expected, (name, host, formatted[host], expected)
            old_wan = '_time:1h {cef.device_product="UniFi Network"} (cef.device_event_class_id:=100 OR cef.extension.msg:"temporary disconnection" OR cef.name:i(failover)) | stats by (cef.extension.UNIFIwanName) count() as disconnects | filter disconnects:>=3 | rename cef.extension.UNIFIwanName as wan'
            new_wan = old_wan.replace('cef.extension.msg:', '_msg:').replace('cef.name:i(failover)',
                       '(cef.name:i(failover) OR (cef.name:="" _msg:i(failover)))')
            expected_wan = [{'disconnects': '3', 'wan': 'WAN1'}, {'disconnects': '3', 'wan': 'WAN2'}]
            assert sorted_rows(query(baseline, old_wan)) == expected_wan
            assert sorted_rows(query(backend, new_wan)) == expected_wan
            result['queries'] = {'conditional_format': conditional, 'unsupported_conditional_copy_errors': copy_errors,
                                 'documentation': 'https://docs.victoriametrics.com/victorialogs/logsql/#conditional-format',
                                 'old_wan': old_wan, 'new_wan': new_wan,
                                 'old_baseline_result': query(baseline, old_wan), 'new_relay_result': query(backend, new_wan),
                                 'old_query_on_new_rows': query(backend, old_wan)}
            rules_path = ROOT / 'kubernetes/apps/observability/victoria-logs-single/app/unifi-alerts.yaml'
            # These are YAML folded expr blocks, bounded by labels at the same indentation.
            rule_expressions = re.findall(r'        - alert: (\w+)\n          expr: >-\n(.*?)\n          labels:', rules_path.read_text(), re.S)
            assert rule_expressions, 'No LogsQL rule expressions found'
            result['queries']['repository_rules'] = {}
            rule_mismatches = []
            for name, lines in rule_expressions:
                expression = ' '.join(line.strip() for line in lines.splitlines())
                old_result = sorted_rows(query(baseline, expression))
                new_result = sorted_rows(query(backend, expression))
                result['queries']['repository_rules'][name] = {'expression': expression, 'baseline_result': old_result,
                                                              'relay_result': new_result}
                if old_result != new_result or not new_result:
                    rule_mismatches.append({'alert': name, 'baseline': old_result, 'relay': new_result})
            result['queries']['rule_mismatches'] = rule_mismatches
            assert not rule_mismatches, ('Repository alert rules differ across schemas, including missing-msg name fallback', rule_mismatches)

            before = metrics(relay_url)
            proxy.available = False
            outage_started = time.monotonic()
            # More than a megabyte of non-compressible synthetic text, with unique IDs.
            rng = random.Random(1530)
            queue_cases = []
            for index in range(args.queue_rows):
                host = f'queue-{index:04d}'
                # Stay below Darwin's default 9216-byte UDP send buffer.
                payload = rng.randbytes(4096).hex()
                queue_cases.append((host, f'<134>1 2020-01-02T03:04:05Z {host} CEF 99 - - CEF:0|Ubiquiti|UniFi Network|10.6.106|900|Queue test|5|msg={host} payload={payload}'))
            send(relay_udp, queue_cases, .005)
            until(lambda: proxy.failures > 0, 'proxy returned HTTP 503')
            until(lambda: metric_sum(metrics(relay_url), 'vl_rows_ingested_total') >= len(cases) + len(queue_cases), 'all outage UDP rows ingested')
            until(lambda: metric_sum(metrics(relay_url), 'vlagent_remotewrite_retries_count_total') > 0, 'retry observed during outage')
            during = metrics(relay_url)
            assert query(backend, 'hostname:queue-*') == [], 'outage unexpectedly reached backend'
            exit_code = relay.stop()
            assert exit_code == 0, ('graceful relay stop', exit_code)
            saved_files = files_snapshot(scratch / 'queue')
            assert sum(saved_files.values()) > 10000, ('No meaningful queue persisted', saved_files)
            assert query(backend, 'hostname:queue-*') == [], 'shutdown unexpectedly reached backend'
            relay = start_relay()
            restarted = metrics(relay_url)
            assert metric_sum(restarted, 'vm_persistentqueue_bytes_read_total') > 10000, ('disk queue not read on restart', restarted)
            time.sleep(.5)
            assert query(backend, 'hostname:queue-*') == [], 'restart bypassed outage'
            proxy.available = True
            outage_seconds = time.monotonic() - outage_started
            queue_rows = until(lambda: (found if len(found := query(backend, 'hostname:queue-*')) >= len(queue_cases) else None),
                               'persisted outage rows recovered after restart', 60)
            # Wait for a second query after drain, so duplicates in another batch are visible.
            until(lambda: metric_sum(metrics(relay_url), 'pending_data_bytes') == 0, 'queue drained', 30)
            time.sleep(1.2)
            queue_rows = query(backend, 'hostname:queue-*')
            counts = Counter(row['hostname'] for row in queue_rows)
            expected_ids = {host for host, _ in queue_cases}
            missing = sorted(expected_ids - counts.keys())
            duplicates = {key: value for key, value in counts.items() if value != 1}
            unexpected = sorted(counts.keys() - expected_ids)
            after = metrics(relay_url)
            result['queue'] = {'sent': len(queue_cases), 'received': len(queue_rows), 'missing': missing,
                               'duplicates': duplicates, 'unexpected': unexpected, 'outage_seconds': round(outage_seconds, 3),
                               'proxy_503_responses': proxy.failures, 'proxy_successes': proxy.successes,
                               'graceful_exit_code': exit_code, 'persisted_files_after_sigterm': saved_files,
                               'metrics_before': before, 'metrics_during_outage': during,
                               'metrics_after_restart_still_outage': restarted, 'metrics_after_drain': after}
            assert not missing and not duplicates and not unexpected, result['queue']
            drop_metrics = {}
            for snapshot in (before, during, restarted, after):
                for key, value in snapshot.items():
                    if 'drop' in key:
                        drop_metrics[key] = max(value, drop_metrics.get(key, 0))
            assert not any(drop_metrics.values()), ('drop metrics nonzero', drop_metrics)
            result['queue']['drop_metrics'] = drop_metrics
            result['queue']['summary'] = {
                'persisted_file_bytes': sum(saved_files.values()),
                'restarted_disk_bytes_read': metric_sum(after, 'vm_persistentqueue_bytes_read_total'),
                'outage_retries': metric_sum(during, 'vlagent_remotewrite_retries_count_total'),
                'pending_bytes_after_drain': metric_sum(after, 'vlagent_remotewrite_pending_data_bytes'),
                'maximum_drop_counter': max(drop_metrics.values(), default=0),
            }
            result['limits'] = [
                'Synthetic UDP loopback only; no production writes or notifications.',
                'Query visibility uses bounded retries with normal backend ingestion; no force_flush or force_merge.',
                'Graceful SIGTERM and same-directory restart tested; no crash/power-loss guarantee.',
                'Pending-byte gauges exclude in-flight requests; disk reread and ID counts establish recovery.',
                'HTTP 503 covers a finite unavailable backend; no saturation or disk-overflow qualification.',
                'UDP remains best effort; this test cannot guarantee delivery before relay receipt.',
                'Binary hashes recorded; supplied binaries were checksum-verified by the parent task.',
            ]
        finally:
            cleanup_errors = []
            for process in reversed(processes):
                try:
                    process.stop()
                except Exception as exc:
                    cleanup_errors.append(f'{process.log.name}: {exc}')
            if proxy:
                try:
                    proxy.close()
                except Exception as exc:
                    cleanup_errors.append(f'proxy: {exc}')
            for log in scratch.glob('*.log'):
                shutil.copyfile(log, args.output / log.name)
            if cleanup_errors:
                result['cleanup_errors'] = cleanup_errors
                raise RuntimeError('Qualification cleanup failed: ' + '; '.join(cleanup_errors))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bin-dir', type=Path, default=PARENT / 'bin')
    parser.add_argument('--output', type=Path, default=Path('/Users/kelchm/.t3/artifacts/cef-relay-qualification'))
    parser.add_argument('--manifest', type=Path, help='Relay YAML with a literal args list; verifies critical fixture flags')
    parser.add_argument('--queue-rows', type=int, default=128)
    args = parser.parse_args()
    assert 64 <= args.queue_rows <= 1000, '--queue-rows must be between 64 and 1000'
    if not args.manifest:
        candidates = list((ROOT / 'kubernetes/apps/observability').glob('*unifi*/app/*.yaml'))
        candidates = [path for path in candidates if 'remoteWrite.headers' in path.read_text()]
        assert len(candidates) <= 1, candidates
        args.manifest = candidates[0] if candidates else None
    args.output.mkdir(parents=True, exist_ok=True)
    result = {'started_at': dt.datetime.now(dt.timezone.utc).isoformat(), 'status': 'running'}
    try:
        qualify(args, result)
        result['status'] = 'passed'
    except Exception:
        result['status'] = 'failed'
        result['error'] = traceback.format_exc()
    finally:
        result['finished_at'] = dt.datetime.now(dt.timezone.utc).isoformat()
        (args.output / 'results.json').write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    print(json.dumps({'status': result['status'], 'results': str(args.output / 'results.json'),
                      'fixtures': result.get('semantics', {}).get('passed'),
                      'queue': {key: result.get('queue', {}).get(key) for key in ('sent', 'received', 'missing', 'duplicates')}}))
    if result['status'] != 'passed':
        print(result['error'])
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
