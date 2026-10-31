"""Copy UniFi Flow View rows into VictoriaLogs.

The console keeps about a day of flow rows and provides a manual CSV export.
This reads the private endpoint its Flow View uses, with the request
the UI sends. Each read covers a fixed time window. The watermark moves only
when every page of a window has been read and VictoriaLogs has accepted the
rows, so delivery is at-least-once: a restart can replay rows since the last
visible checkpoint, which share a flow.id. Overlap re-reads recent time to pick
up late flows, but arrivals outside it can be missed. Detected pagination
failures hold the watermark; time deliberately abandoned is recorded as a gap.

Stdlib only. Row contents, credentials, cookies and tokens are never logged.
Operations: docs/runbooks/logging.md.
"""

import dataclasses
import datetime
import hashlib
import http.client
import http.cookiejar
import http.server
import json
import os
import signal
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

SERVICE = 'unifi-flows'
FLOWS_PATH = '/proxy/network/v2/api/site/{site}/traffic-flows'
LOGIN_PATH = '/api/auth/login'
# The UI sends every filter, empty when unset.
FILTERS = (
    'risk', 'action', 'direction', 'protocol', 'policy', 'policy_type', 'service',
    'source_host', 'source_mac', 'source_ip', 'source_port', 'source_network_id', 'source_domain', 'source_zone_id', 'source_region',
    'destination_host', 'destination_mac', 'destination_ip', 'destination_port', 'destination_network_id', 'destination_domain', 'destination_zone_id', 'destination_region',
    'in_network_id', 'out_network_id', 'next_ai_query', 'except_for',
)
PAGE_SIZE = 100
MAX_BODY_BYTES = 4 * 1024 * 1024
MAX_BATCH_BYTES = 1024 * 1024
MAX_LOGIN_BACKOFF_S = 3600
RESULTS = ('ok', 'no_rows', 'backlog', 'incomplete', 'auth_failed', 'access_denied', 'rate_limited', 'read_failed', 'ingest_failed', 'state_unavailable')


@dataclasses.dataclass
class Config:
    console_url: str = 'https://unifi.home.kelch.io'
    site: str = 'default'
    username: str = 'unpoller-sa'
    password_file: str = '/secrets/password'
    logs_url: str = 'http://victoria-logs-single-server.observability.svc.cluster.local:9428'
    metrics_port: int = 9464
    poll_s: int = 300
    # Re-read this much time before the watermark, for flows that appear late.
    overlap_s: int = 120
    # Stop this far short of now, so a window's end is settled when it is read.
    settle_s: int = 30
    # A first run starts this far back rather than copying the whole store.
    initial_lookback_s: int = 900
    # Under the store's 25 to 28 hours, so a recovered window still has its rows.
    max_catchup_s: int = 72000
    # A busy 10 minutes is about 3,300 rows, well under the query cap.
    max_window_s: int = 600
    min_window_s: int = 30
    query_cap_rows: int = 10000
    max_pages_per_cycle: int = 200
    page_delay_s: float = 0.1
    max_cycle_s: int = 240
    request_timeout_s: int = 20

    @classmethod
    def from_env(cls):
        config = cls()
        for field in dataclasses.fields(cls):
            value = os.environ.get(f'UNIFI_FLOWS_{field.name.upper()}')
            if value is not None:
                setattr(config, field.name, type(getattr(config, field.name))(value))
        return config


class CycleError(Exception):
    """A cycle ended early. The class names the result the metrics report."""
    result = 'read_failed'


class AuthFailed(CycleError):
    result = 'auth_failed'


class AccessDenied(CycleError):
    result = 'access_denied'


class RateLimited(CycleError):
    result = 'rate_limited'


class ReadFailed(CycleError):
    result = 'read_failed'


class IngestFailed(CycleError):
    result = 'ingest_failed'


class StateUnavailable(CycleError):
    result = 'state_unavailable'


def log(level, msg, **fields):
    print(json.dumps({'level': level, 'msg': msg, **fields}), flush=True)


def iso(ms):
    moment = datetime.datetime.fromtimestamp(ms / 1000, datetime.timezone.utc)
    return moment.isoformat(timespec='milliseconds').replace('+00:00', 'Z')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class Console:
    """One process-local session on the UniFi console. Retries only a read its ended session refused."""

    def __init__(self, config, clock=time.time):
        self.config = config
        self.clock = clock
        self.jar = http.cookiejar.CookieJar()
        self.tls = ssl.create_default_context()
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), NoRedirect(),
            urllib.request.HTTPCookieProcessor(self.jar), urllib.request.HTTPSHandler(context=self.tls))
        self.csrf = None
        self.fresh = False
        self.login_after = 0
        self.backoff_s = 0
        self.refusal = AuthFailed
        self.requests_after = 0
        self.logins = {'ok': 0, 'failed': 0}

    def begin_cycle(self):
        self.fresh = False

    def _post(self, path, payload, headers=None):
        request = urllib.request.Request(
            self.config.console_url + path, data=json.dumps(payload).encode(), method='POST',
            headers={'Content-Type': 'application/json', 'Accept': 'application/json', **(headers or {})})
        try:
            with self.opener.open(request, timeout=self.config.request_timeout_s) as response:
                return response.status, response.headers, response.read(MAX_BODY_BYTES + 1)
        except urllib.error.HTTPError as error:
            with error:
                return error.code, error.headers, b''
        except (OSError, http.client.HTTPException) as error:
            raise ReadFailed(type(error).__name__) from None

    def _refuse(self, refusal):
        """Space out refused attempts to reduce authentication pressure."""
        self.refusal = refusal
        self.backoff_s = min(max(self.backoff_s * 2, self.config.poll_s), MAX_LOGIN_BACKOFF_S)
        self.login_after = self.clock() + self.backoff_s

    def _rate_limited(self, headers):
        try:
            wait = int(headers.get('Retry-After') or 0)
        except ValueError:
            wait = 0
        self.requests_after = self.clock() + min(max(wait, self.config.poll_s), MAX_LOGIN_BACKOFF_S)
        raise RateLimited('http_429')

    def _take_token(self, headers):
        token = headers.get('x-updated-csrf-token') or headers.get('x-csrf-token')
        if token:
            self.csrf = token

    def drop_session(self):
        self.jar.clear()
        self.csrf = None

    def login(self):
        if self.clock() < self.login_after:
            raise self.refusal('login_backoff')
        try:
            with open(self.config.password_file, encoding='utf-8') as secret:
                password = secret.read().rstrip('\n')
        except OSError as error:
            raise AuthFailed(type(error).__name__) from None
        status, headers, _ = self._post(LOGIN_PATH, {'username': self.config.username, 'password': password})
        del password
        if status == 429:
            self._rate_limited(headers)
        self._take_token(headers)
        if status != 200 or not self.csrf:
            self.logins['failed'] += 1
            self.drop_session()
            self._refuse(AuthFailed)
            raise AuthFailed(f'http_{status}')
        self.logins['ok'] += 1
        self.fresh = True

    def flows(self, start_ms, end_ms, page):
        if self.clock() < self.requests_after:
            raise RateLimited('retry_after')
        if not self.csrf:
            self.login()
        body = {**{name: [] for name in FILTERS}, 'timestampFrom': start_ms, 'timestampTo': end_ms, 'pageNumber': page, 'search_text': '', 'pageSize': PAGE_SIZE, 'skip_count': False}
        status, headers, raw = self._post(FLOWS_PATH.format(site=self.config.site), body, {'X-CSRF-Token': self.csrf})
        if status in (401, 403):
            reused = not self.fresh
            self.drop_session()
            if reused:
                # The console ends a session after about two hours, however busy it is.
                return self.flows(start_ms, end_ms, page)
            # A session made this cycle was refused, so logging in again cannot help.
            self._refuse(AccessDenied)
            raise AccessDenied(f'http_{status}')
        if status == 429:
            self._rate_limited(headers)
        if status != 200:
            raise ReadFailed(f'http_{status}')
        if len(raw) > MAX_BODY_BYTES:
            raise ReadFailed('oversized_body')
        self._take_token(headers)
        try:
            document = json.loads(raw)
        except ValueError:
            raise ReadFailed('invalid_json') from None
        self.backoff_s = 0
        return document


class Sink:
    """VictoriaLogs' JSON lines endpoint. A 2xx means the rows were accepted."""

    def __init__(self, config):
        self.config = config
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def _open(self, url, data=None):
        # urllib's default form type would make VictoriaLogs parse the body as a form.
        request = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/stream+json'} if data else {})
        try:
            with self.opener.open(request, timeout=self.config.request_timeout_s) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            with error:
                return error.code, b''

    def write(self, lines):
        query = urllib.parse.urlencode({'_stream_fields': 'service.name,record.type', '_time_field': '_time', '_msg_field': '_msg'})
        try:
            status, _ = self._open(f'{self.config.logs_url}/insert/jsonline?{query}', '\n'.join(lines).encode() + b'\n')
        except (OSError, http.client.HTTPException) as error:
            raise IngestFailed(type(error).__name__) from None
        if not 200 <= status < 300:
            raise IngestFailed(f'http_{status}')

    def last_watermark(self):
        """The newest checkpoint's watermark in ms, or None if there has never been one."""
        logsql = f'{{service.name="{SERVICE}",record.type="checkpoint"}} | sort by (_time desc) | limit 1 | fields checkpoint.watermark'
        query = urllib.parse.urlencode({'query': logsql, 'start': '31d'})
        try:
            status, raw = self._open(f'{self.config.logs_url}/select/logsql/query?{query}')
        except (OSError, http.client.HTTPException) as error:
            raise StateUnavailable(type(error).__name__) from None
        if status != 200:
            raise StateUnavailable(f'http_{status}')
        for line in raw.splitlines():
            if line.strip():
                try:
                    return int(json.loads(line)['checkpoint.watermark'])
                except (ValueError, KeyError):
                    raise StateUnavailable('invalid_checkpoint') from None
        return None


def flow_id(row):
    if isinstance(row.get('id'), str) and row['id']:
        return row['id']
    source, destination = row.get('source') or {}, row.get('destination') or {}
    key = (row.get('time'), row.get('protocol'), source.get('ip'), source.get('port'), destination.get('ip'), destination.get('port'))
    return 'derived-' + hashlib.sha256(repr(key).encode()).hexdigest()[:24]


def flow_scope(source_network, destination_network, destination_zone):
    if destination_zone == 'External':
        return 'internet'
    if destination_zone == 'Gateway':
        return 'gateway'
    return 'same_network' if source_network == destination_network else 'inter_network'


def flow_record(row):
    """Map one Flow View row onto the stored schema. Only named fields are kept."""
    source, destination = row.get('source') or {}, row.get('destination') or {}
    traffic = row.get('traffic_data') or {}
    incoming, outgoing = row.get('in') or {}, row.get('out') or {}
    source_zone, destination_zone = source.get('zone_name'), destination.get('zone_name')
    source_network = incoming.get('network_name') or source.get('network_name') or source_zone or 'unknown'
    destination_network = outgoing.get('network_name') or destination.get('network_name') or destination_zone or 'unknown'
    action, transport = row.get('action'), str(row.get('protocol') or 'unknown').lower()
    policies = sorted({str(p.get('name') or p.get('internal_type')) for p in row.get('policies') or [] if isinstance(p, dict)})
    start = row.get('flow_start_time')
    record = {
        '_time': iso(row['time']),
        '_msg': f'{action} {transport} {source_network} to {destination_network}',
        'service.name': SERVICE,
        'record.type': 'flow',
        'flow.id': flow_id(row),
        'flow.scope': flow_scope(source_network, destination_network, destination_zone),
        'flow.action': action,
        'flow.risk': row.get('risk'),
        'flow.direction': row.get('direction'),
        'flow.service': row.get('service'),
        'flow.count': row.get('count'),
        'flow.start': iso(start) if isinstance(start, (int, float)) else None,
        'flow.duration_ms': row.get('duration_milliseconds'),
        'flow.policies': ','.join(policies),
        'network.transport': transport,
        'source.network': source_network,
        'source.zone': source_zone,
        'source.address': source.get('ip'),
        'source.port': source.get('port'),
        'source.name': source.get('client_name') or source.get('device_name') or source.get('host_name'),
        'destination.network': destination_network,
        'destination.zone': destination_zone,
        'destination.address': destination.get('ip'),
        'destination.port': destination.get('port'),
        'destination.name': destination.get('client_name') or destination.get('device_name') or destination.get('host_name'),
        'destination.domain': ','.join(str(d) for d in destination.get('domains') or []),
        'network.bytes': traffic.get('bytes_total'),
        'network.bytes_sent': traffic.get('bytes_tx'),
        'network.bytes_received': traffic.get('bytes_rx'),
        'network.packets': traffic.get('packets_total'),
    }
    return {k: v for k, v in record.items() if v not in (None, '')}


@dataclasses.dataclass
class Window:
    """What one fixed time window returned. problem is None only when it was read in full."""
    rows: dict
    problem: str | None = None
    capped: bool = False
    shifted: bool = False


class Budget:
    """Bounds on one cycle's work."""

    def __init__(self, pages, deadline):
        self.pages, self.deadline = pages, deadline

    def spent(self, clock):
        return self.pages <= 0 or clock() >= self.deadline


class Collector:
    def __init__(self, config, console=None, sink=None, clock=time.time, wait=None):
        self.config = config
        self.clock = clock
        self.console = console or Console(config, clock)
        self.sink = sink or Sink(config)
        self.stop = threading.Event()
        self.wait = wait or self.stop.wait
        self.watermark = None
        self.window_ms = config.max_window_s * 1000
        self.seen = {}
        self.primed = False
        self.pending = []
        self.lock = threading.Lock()
        self.heartbeat = clock()
        self.counters = dict.fromkeys(('rows_read', 'rows_written', 'rows_duplicate', 'rows_late', 'pages_read', 'windows_shifted', 'gap_seconds', 'ingest_failures'), 0)
        self.cycles = dict.fromkeys(RESULTS, 0)
        self.problems = {}
        self.last_result = None
        self.last_success = 0
        self.last_gap = 0

    def count(self, name, amount=1):
        with self.lock:
            self.counters[name] += amount

    def checkpoint(self, result):
        return json.dumps({'_time': iso(self.clock() * 1000), '_msg': f'checkpoint {result}', 'service.name': SERVICE, 'record.type': 'checkpoint', 'checkpoint.watermark': str(self.watermark), 'checkpoint.result': result})

    def gap(self, start_ms, end_ms, reason):
        seconds = max(end_ms - start_ms, 0) / 1000
        self.count('gap_seconds', seconds)
        with self.lock:
            self.last_gap = self.clock()
        self.pending.append(json.dumps({'_time': iso(self.clock() * 1000), '_msg': f'coverage gap {reason}', 'service.name': SERVICE, 'record.type': 'gap', 'gap.start': iso(start_ms), 'gap.end': iso(end_ms), 'gap.seconds': seconds, 'gap.reason': reason}))
        log('warning', 'coverage gap', reason=reason, start=iso(start_ms), end=iso(end_ms), seconds=seconds)

    def read_window(self, start_ms, end_ms, budget, accept_cap=False):
        """Read one window page by page, and say exactly how the read fell short."""
        window = Window({})
        previous = None
        first_total = None
        page = 0
        while True:
            if budget.spent(self.clock) or self.stop.is_set():
                window.problem = 'cycle_budget'
                return window
            document = self.console.flows(start_ms, end_ms, page)
            budget.pages -= 1
            self.count('pages_read')
            data = document.get('data') if isinstance(document, dict) else None
            if not isinstance(data, list):
                window.problem = 'unexpected_shape'
                return window
            if (any(type(document.get(key)) is not int for key in ('total_element_count', 'total_page_count', 'page_number'))
                    or any(type(document.get(key)) is not bool for key in ('has_next', 'or_more'))):
                window.problem = 'invalid_pagination'
                return window
            if document.get('page_number') != page:
                window.problem = 'ignored_page'
                return window
            total = document['total_element_count']
            pages = document['total_page_count']
            if (total < 0 or pages < 0
                    or (total == 0 and (data or page != 0 or pages not in (0, 1) or document['has_next'] or document['or_more']))
                    or (total > 0 and (pages != (total + PAGE_SIZE - 1) // PAGE_SIZE
                        or page >= pages or document['has_next'] != (page + 1 < pages)
                        or len(data) != min(PAGE_SIZE, total - page * PAGE_SIZE)))
                    or (document['or_more'] and total < self.config.query_cap_rows)):
                window.problem = 'invalid_pagination'
                return window
            if first_total is not None and total != first_total:
                window.shifted = True
                window.problem = 'changing_total'
                return window
            first_total = total
            if page == 0 and (document.get('or_more') or total >= self.config.query_cap_rows):
                window.capped = True
                if not accept_cap:
                    return window
            ids = []
            for row in data:
                if not isinstance(row, dict) or not isinstance(row.get('time'), int) or isinstance(row.get('time'), bool):
                    window.problem = 'unexpected_row'
                    return window
                if not start_ms <= row['time'] <= end_ms:
                    window.problem = 'window_ignored'
                    return window
                ids.append(flow_id(row))
            if page > 0 and ids and (ids == previous or not set(ids) - set(window.rows)):
                window.problem = 'repeated_page'
                return window
            window.rows.update(zip(ids, data))
            previous = ids
            if not document.get('has_next'):
                break
            if not data:
                window.problem = 'empty_page'
                return window
            page += 1
            if page * PAGE_SIZE >= self.config.query_cap_rows:
                window.capped = True
                break
            if self.config.page_delay_s:
                self.wait(self.config.page_delay_s)
        if not window.capped and len(window.rows) != first_total:
            window.problem = 'short_count'
        return window

    def store(self, window):
        """Send unseen rows. Rows join seen only once accepted, so a failure resends them."""
        self.count('rows_read', len(window.rows))
        fresh, batch, size = {}, [], 0
        floor = self.watermark

        def flush():
            nonlocal batch, size
            if batch:
                self.sink.write(batch)
                self.seen.update(fresh)
                self.count('rows_written', len(batch))
                fresh.clear()
                batch, size = [], 0

        for identity, row in window.rows.items():
            if identity in self.seen:
                self.count('rows_duplicate')
                continue
            if self.primed and row['time'] < floor:
                self.count('rows_late')
            line = json.dumps(flow_record(row), separators=(',', ':'))
            if size + len(line) > MAX_BATCH_BYTES:
                flush()
            batch.append(line)
            size += len(line) + 1
            fresh[identity] = row['time']
        flush()

    def advance(self, budget):
        """Move the watermark as far as this cycle's budget and the console allow."""
        end_limit = int((self.clock() - self.config.settle_s) * 1000)
        oldest = end_limit - self.config.max_catchup_s * 1000
        if self.watermark < oldest:
            self.gap(self.watermark, oldest, 'beyond_catchup')
            self.watermark = oldest
        rows = 0
        while self.watermark < end_limit:
            end = min(self.watermark + self.window_ms, end_limit)
            start = max(self.watermark - self.config.overlap_s * 1000, 0)
            at_floor = self.window_ms <= self.config.min_window_s * 1000
            window = self.read_window(start, end, budget, accept_cap=at_floor)
            if window.capped and not at_floor:
                self.window_ms = max(self.window_ms // 2, self.config.min_window_s * 1000)
                continue
            self.store(window)
            rows += len(window.rows)
            if window.shifted:
                self.count('windows_shifted')
            if window.problem == 'cycle_budget':
                return 'backlog'
            if window.problem:
                self.problems[window.problem] = self.problems.get(window.problem, 0) + 1
                log('warning', 'window incomplete', problem=window.problem, start=iso(start), end=iso(end))
                return 'incomplete'
            if window.capped:
                # Still over the cap at the smallest window: the rows are newest first.
                oldest_row = min(row['time'] for row in window.rows.values())
                self.gap(self.watermark, oldest_row, 'query_cap')
            self.watermark = end
            self.primed = True
            self.seen = {k: v for k, v in self.seen.items() if v >= end - 2 * self.config.overlap_s * 1000}
            self.window_ms = min(self.window_ms * 2, self.config.max_window_s * 1000)
        return 'ok' if rows else 'no_rows'

    def restore(self):
        if self.watermark is not None:
            return
        stored = self.sink.last_watermark()
        self.watermark = stored if stored is not None else int((self.clock() - self.config.initial_lookback_s) * 1000)
        log('info', 'resuming', watermark=iso(self.watermark), from_checkpoint=stored is not None)

    def cycle(self):
        """One poll. Returns the result the metrics report."""
        self.console.begin_cycle()
        # Always room for a whole window, or a full one could never finish.
        pages = max(self.config.max_pages_per_cycle, self.config.query_cap_rows // PAGE_SIZE + 1)
        budget = Budget(pages, self.clock() + self.config.max_cycle_s)
        before = self.watermark
        detail = None
        try:
            self.restore()
            result = self.advance(budget)
        except CycleError as error:
            result, detail = error.result, str(error)
        except (AttributeError, KeyError, TypeError, ValueError) as error:
            # A changed row shape must not crash-loop into repeated logins. The
            # exception type alone is logged: its text could quote a row.
            result, detail = 'incomplete', type(error).__name__
            self.problems['unexpected_row'] = self.problems.get('unexpected_row', 0) + 1
        if self.watermark is not None and (self.watermark != before or self.pending):
            try:
                self.sink.write([*self.pending, self.checkpoint(result)])
                self.pending = []
            except IngestFailed as error:
                detail = str(error)
                if result in ('ok', 'no_rows', 'backlog'):
                    result = 'ingest_failed'
        if result == 'ingest_failed':
            self.count('ingest_failures')
        with self.lock:
            self.cycles[result] += 1
            self.last_result = result
            if result in ('ok', 'no_rows'):
                self.last_success = self.clock()
            self.heartbeat = self.clock()
        log('info' if result in ('ok', 'no_rows', 'backlog') else 'warning', 'cycle', result=result, detail=detail, watermark=iso(self.watermark) if self.watermark is not None else None, **self.counters)
        return result

    def metrics(self):
        with self.lock:
            counters, cycles, problems = dict(self.counters), dict(self.cycles), dict(self.problems)
            last, success, watermark, last_gap = self.last_result, self.last_success, self.watermark, self.last_gap
        logins = self.console.logins
        lines = []
        for name, kind, help_text, samples in (
            ('unifi_flows_cycles_total', 'counter', 'Poll cycles by result.', [(f'result="{r}"', cycles[r]) for r in RESULTS]),
            ('unifi_flows_last_cycle_result', 'gauge', '1 for the latest cycle result.', [(f'result="{r}"', int(r == last)) for r in RESULTS]),
            ('unifi_flows_incomplete_windows_total', 'counter', 'Windows that could not be read in full, by reason.', [(f'problem="{p}"', n) for p, n in sorted(problems.items())]),
            ('unifi_flows_logins_total', 'counter', 'Console logins by result.', [(f'result="{r}"', logins[r]) for r in ('ok', 'failed')]),
            ('unifi_flows_pages_read_total', 'counter', 'Flow pages read from the console.', [('', counters['pages_read'])]),
            ('unifi_flows_rows_read_total', 'counter', 'Rows read from the console.', [('', counters['rows_read'])]),
            ('unifi_flows_rows_written_total', 'counter', 'Rows VictoriaLogs accepted.', [('', counters['rows_written'])]),
            ('unifi_flows_rows_duplicate_total', 'counter', 'Rows skipped as already sent.', [('', counters['rows_duplicate'])]),
            ('unifi_flows_rows_late_total', 'counter', 'New rows older than the watermark, found by overlap.', [('', counters['rows_late'])]),
            ('unifi_flows_windows_shifted_total', 'counter', 'Windows whose row count changed between pages.', [('', counters['windows_shifted'])]),
            ('unifi_flows_gap_seconds_total', 'counter', 'Time abandoned as a coverage gap.', [('', counters['gap_seconds'])]),
            ('unifi_flows_last_gap_timestamp_seconds', 'gauge', 'Time of the latest gap detected by this process, or zero.', [('', last_gap)]),
            ('unifi_flows_ingest_failures_total', 'counter', 'Cycles VictoriaLogs did not accept.', [('', counters['ingest_failures'])]),
            ('unifi_flows_watermark_timestamp_seconds', 'gauge', 'Newest time read in full and accepted.', [('', (watermark or 0) / 1000)]),
            ('unifi_flows_last_success_timestamp_seconds', 'gauge', 'End of the latest ok or no_rows cycle.', [('', success)]),
        ):
            lines += [f'# HELP {name} {help_text}', f'# TYPE {name} {kind}']
            lines += [f'{name}{{{labels}}} {value}' if labels else f'{name} {value}' for labels, value in samples]
        return '\n'.join(lines) + '\n'

    def healthy(self):
        with self.lock:
            return self.clock() - self.heartbeat < 3 * self.config.poll_s + self.config.max_cycle_s

    def run(self):
        while not self.stop.is_set():
            self.cycle()
            self.wait(self.config.poll_s)


def serve(collector, port):
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == '/metrics':
                status, body = 200, collector.metrics().encode()
            elif self.path == '/healthz':
                status, body = (200, b'ok\n') if collector.healthy() else (503, b'stalled\n')
            else:
                status, body = 404, b''
            self.send_response(status)
            self.send_header('Content-Type', 'text/plain; version=0.0.4')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(('', port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def main():
    collector = Collector(Config.from_env())
    for name in (signal.SIGTERM, signal.SIGINT):
        signal.signal(name, lambda *_: collector.stop.set())
    serve(collector, collector.config.metrics_port)
    log('info', 'started', poll_s=collector.config.poll_s, overlap_s=collector.config.overlap_s)
    collector.run()
    log('info', 'stopped')


if __name__ == '__main__':
    sys.exit(main())
