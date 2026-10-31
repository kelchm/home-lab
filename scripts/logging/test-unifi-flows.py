#!/usr/bin/env python3
"""Exercise the UniFi flow collector against fake console and VictoriaLogs servers.

The collector code, HTTP clients and JSON handling are the real ones. Only the
two servers are fakes, driven from synthetic rows, so nothing here needs a
credential or a network. test-unifi-flows-queries.py covers the real VictoriaLogs.
"""
import contextlib
import copy
import http.server
import importlib.util
import io
import json
import ssl
import tempfile
import threading
import unittest
from unittest import mock
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('collector', ROOT / 'kubernetes/apps/observability/unifi-flows/app/collector.py')
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)
TEMPLATES = json.loads((ROOT / 'scripts/logging/unifi-flows-rows.json').read_text())
PASSWORD = 'CANARY-PASSWORD'
NOW = 1_800_000_000.0


class FakeConsole(http.server.ThreadingHTTPServer):
    """The console as observed: newest rows first, 100 to a page, 10,000 at most."""

    def __init__(self):
        super().__init__(('127.0.0.1', 0), ConsoleHandler)
        self.rows = []
        self.login_status = 200
        self.logins = 0
        self.flow_requests = []
        self.sessions = set()
        self.refuse_flows = None
        self.expire_session_at_page = None
        self.fail_page = None
        self.fail_status = 500
        self.ignore_page = False
        self.freeze_page_number = False
        self.ignore_window = False
        self.understate_pages = False
        self.insert_during_page = None
        self.retry_after = None

    def page(self, body):
        number = body['pageNumber']
        if self.insert_during_page and number == self.insert_during_page[0]:
            self.rows.append(self.insert_during_page[1])
            self.insert_during_page = None
        rows = self.rows if self.ignore_window else [r for r in self.rows if body['timestampFrom'] <= r['time'] <= body['timestampTo']]
        rows = sorted(rows, key=lambda r: r['time'], reverse=True)
        total = min(len(rows), 10000)
        served = 0 if self.ignore_page else number
        data = rows[:10000][served * 100:(served + 1) * 100]
        has_next = (served + 1) * 100 < total
        if self.understate_pages:
            has_next = False
        return {'data': data, 'has_next': has_next, 'or_more': len(rows) > 10000, 'page_number': 0 if self.freeze_page_number else number, 'total_element_count': total, 'total_page_count': (total + 99) // 100}


class ConsoleHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, status, body=b'', headers=()):
        self.send_response(status)
        for name, value in headers:
            self.send_header(name, value)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        fake = self.server
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        if self.path == '/api/auth/login':
            fake.logins += 1
            assert body == {'username': 'unpoller-sa', 'password': PASSWORD}
            if fake.login_status != 200:
                return self.reply(fake.login_status)
            token = f'CANARY-CSRF-{fake.logins}'
            fake.sessions.add(token)
            return self.reply(200, b'{}', [('Set-Cookie', f'TOKEN=CANARY-COOKIE-{fake.logins}; Path=/; HttpOnly'), ('x-csrf-token', token)])
        assert self.path == '/proxy/network/v2/api/site/default/traffic-flows'
        fake.flow_requests.append(body)
        token = self.headers.get('X-CSRF-Token')
        if token not in fake.sessions or f'TOKEN=CANARY-COOKIE-{token.rsplit("-", 1)[-1]}' not in (self.headers.get('Cookie') or ''):
            return self.reply(401)
        if fake.refuse_flows:
            return self.reply(fake.refuse_flows)
        if fake.expire_session_at_page == body['pageNumber']:
            fake.expire_session_at_page = None
            fake.sessions.clear()
            return self.reply(401)
        if fake.fail_page == body['pageNumber']:
            fake.fail_page = None
            headers = [('Retry-After', str(fake.retry_after))] if fake.retry_after else []
            if fake.fail_status == 302:
                headers = [('Location', '/elsewhere')]
            return self.reply(fake.fail_status, b'', headers)
        return self.reply(200, json.dumps(fake.page(body)).encode(), [('Content-Type', 'application/json')])


class FakeLogs(http.server.ThreadingHTTPServer):
    def __init__(self):
        super().__init__(('127.0.0.1', 0), LogsHandler)
        self.records = []
        self.fail_writes = 0
        self.fail_reads = False
        self.params = []
        self.content_types = []

    def of_type(self, kind):
        return [r for r in self.records if r['record.type'] == kind]

    def flow_ids(self):
        return [r['flow.id'] for r in self.of_type('flow')]


class LogsHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, status, body=b''):
        self.send_response(status)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        fake = self.server
        url = urllib.parse.urlsplit(self.path)
        body = self.rfile.read(int(self.headers['Content-Length']))
        assert url.path == '/insert/jsonline'
        if fake.fail_writes:
            fake.fail_writes -= 1
            return self.reply(503)
        fake.params.append(urllib.parse.parse_qs(url.query))
        fake.content_types.append(self.headers['Content-Type'])
        fake.records += [json.loads(line) for line in body.splitlines()]
        self.reply(204)

    def do_GET(self):
        fake = self.server
        url = urllib.parse.urlsplit(self.path)
        if url.path != '/select/logsql/query':
            return self.reply(404)
        if fake.fail_reads:
            return self.reply(503)
        points = fake.of_type('checkpoint')
        self.reply(200, (json.dumps({'checkpoint.watermark': points[-1]['checkpoint.watermark']}) + '\n').encode() if points else b'')


def row(number, at_ms, template=0):
    made = copy.deepcopy(TEMPLATES[template])
    made.update(id=f'{number:024x}', time=at_ms, flow_end_time=at_ms, flow_start_time=at_ms - 1000)
    return made


class Harness(unittest.TestCase):
    def setUp(self):
        self.console = FakeConsole()
        self.logs = FakeLogs()
        for server in (self.console, self.logs):
            threading.Thread(target=server.serve_forever, daemon=True).start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
        self.now = NOW
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        secret = Path(self.dir.name, 'password')
        secret.write_text(PASSWORD + '\n')
        self.config = collector.Config(
            console_url=f'http://127.0.0.1:{self.console.server_address[1]}', logs_url=f'http://127.0.0.1:{self.logs.server_address[1]}',
            password_file=str(secret), page_delay_s=0, request_timeout_s=3)
        self.out = io.StringIO()

    def collector(self):
        return collector.Collector(self.config, clock=lambda: self.now, wait=lambda seconds: None)

    def cycle(self, instance):
        with contextlib.redirect_stdout(self.out):
            return instance.cycle()

    def edge(self):
        """The newest time a cycle at self.now reads up to, in ms."""
        return int((self.now - self.config.settle_s) * 1000)

    def recent(self, count, age_s=300):
        """Rows spread across the age_s before the cycle's end."""
        return [row(i + 1, self.edge() - 1000 - i * (age_s * 1000 // max(count, 1))) for i in range(count)]


class Reads(Harness):
    def test_gap_before_first_scrape_has_a_timestamp(self):
        instance = self.collector()
        with contextlib.redirect_stdout(self.out):
            instance.gap(self.edge() - 3600000, self.edge(), 'beyond_catchup')
        self.assertIn(f'unifi_flows_last_gap_timestamp_seconds {self.now}', instance.metrics())
        self.assertEqual(instance.counters['gap_seconds'], 3600)

    def test_multipage_read_stores_each_row_once_and_advances(self):
        self.console.rows = self.recent(250, age_s=500)
        instance = self.collector()
        self.assertEqual(self.cycle(instance), 'ok')
        self.assertEqual(sorted(self.logs.flow_ids()), sorted(r['id'] for r in self.console.rows))
        self.assertEqual(instance.watermark, self.edge())
        self.assertEqual({r['pageNumber'] for r in self.console.flow_requests}, {0, 1})
        windows = {r['timestampFrom']: set() for r in self.console.flow_requests}
        for request in self.console.flow_requests:
            windows[request['timestampFrom']].add(request['timestampTo'])
        self.assertTrue(all(len(ends) == 1 for ends in windows.values()), 'timestampTo is fixed for each window')
        self.assertEqual(instance.counters['pages_read'], len(self.console.flow_requests))
        self.assertEqual(self.logs.of_type('checkpoint')[-1]['checkpoint.watermark'], str(self.edge()))
        self.assertEqual(self.logs.params[0]['_stream_fields'], ['service.name,record.type'])
        self.assertEqual(set(self.logs.content_types), {'application/stream+json'})

    def test_request_is_the_proved_ui_shape(self):
        self.cycle(self.collector())
        body = self.console.flow_requests[0]
        scalars = {k: body.pop(k) for k in ('timestampFrom', 'timestampTo', 'pageNumber', 'search_text', 'pageSize', 'skip_count')}
        self.assertEqual({k: scalars[k] for k in ('pageNumber', 'search_text', 'pageSize', 'skip_count')}, {'pageNumber': 0, 'search_text': '', 'pageSize': 100, 'skip_count': False})
        self.assertEqual(body, {name: [] for name in collector.FILTERS})
        self.assertEqual(len(body), 27)

    def test_zero_rows_is_a_success_not_a_failure(self):
        instance = self.collector()
        self.assertEqual(self.cycle(instance), 'no_rows')
        self.assertEqual(instance.watermark, self.edge())
        self.assertIn('unifi_flows_last_cycle_result{result="no_rows"} 1', instance.metrics())

    def test_contradictory_empty_page_metadata_holds_the_watermark(self):
        patches = [
            {'total_element_count': -1}, {'total_element_count': True},
            {'total_page_count': 5}, {'total_page_count': -1},
            {'has_next': True}, {'has_next': 0}, {'or_more': True},
            {'page_number': False},
        ]
        original = self.console.page
        for changes in patches:
            with self.subTest(changes=changes):
                with mock.patch.object(self.console, 'page', side_effect=lambda body: {**original(body), **changes}):
                    instance = self.collector()
                    self.assertEqual(self.cycle(instance), 'incomplete')
                    self.assertEqual(instance.problems, {'invalid_pagination': 1})
                    self.assertEqual(instance.watermark, int((self.now - self.config.initial_lookback_s) * 1000))
        self.assertEqual(self.logs.flow_ids(), [])

    def test_decreasing_total_cannot_hide_an_unread_row(self):
        self.config.initial_lookback_s = 300
        self.console.rows = self.recent(101, age_s=250)
        original = self.console.page

        def disappearing_row(body):
            document = original(body)
            if body['pageNumber'] == 1:
                document.update(data=[], total_element_count=100, total_page_count=1, has_next=False)
            return document

        with mock.patch.object(self.console, 'page', side_effect=disappearing_row):
            instance = self.collector()
            self.assertEqual(self.cycle(instance), 'incomplete')
            self.assertEqual(instance.watermark, int((self.now - self.config.initial_lookback_s) * 1000))
            self.assertEqual(len(self.logs.flow_ids()), 100)

    def test_window_over_the_query_cap_is_narrowed_not_truncated(self):
        self.config.max_window_s = 600
        self.config.initial_lookback_s = 600
        self.config.max_pages_per_cycle = 400
        self.config.max_cycle_s = 100000
        start = self.edge() - 600_000
        self.console.rows = [row(i + 1, start + 1 + i * 45) for i in range(12500)]
        instance = self.collector()
        self.assertEqual(self.cycle(instance), 'ok')
        self.assertEqual(len(set(self.logs.flow_ids())), 12500)
        self.assertEqual(self.logs.of_type('gap'), [])
        self.assertTrue(all(r['pageNumber'] < 100 for r in self.console.flow_requests))

    def test_unsplittable_cap_records_a_gap_instead_of_claiming_complete(self):
        self.config.initial_lookback_s = 10
        self.config.overlap_s = 0
        self.config.settle_s = 0
        self.config.max_window_s = 30
        self.config.max_pages_per_cycle = 500
        self.config.max_cycle_s = 100000
        end = self.edge()
        self.console.rows = [row(i + 1, end - 9000 + i % 8000) for i in range(10500)]
        instance = self.collector()
        self.assertEqual(self.cycle(instance), 'ok')
        gaps = self.logs.of_type('gap')
        self.assertEqual([g['gap.reason'] for g in gaps], ['query_cap'])
        self.assertLessEqual(len(set(self.logs.flow_ids())), 10000)

    def test_ignored_page_number_is_incomplete(self):
        self.console.rows = self.recent(250)
        self.console.freeze_page_number = True
        instance = self.collector()
        self.assertEqual(self.cycle(instance), 'incomplete')
        self.assertEqual(instance.problems, {'ignored_page': 1})
        self.assertLess(instance.watermark, self.edge())
        self.assertIn('problem="ignored_page"', instance.metrics())

    def test_repeated_page_is_incomplete(self):
        self.console.rows = self.recent(250)
        self.console.ignore_page = True
        instance = self.collector()
        self.assertEqual(self.cycle(instance), 'incomplete')
        self.assertEqual(instance.problems, {'repeated_page': 1})
        self.assertLess(instance.watermark, self.edge())

    def test_ignored_time_window_is_incomplete(self):
        self.console.rows = [row(1, self.edge() - 5_000_000)]
        self.console.ignore_window = True
        instance = self.collector()
        self.assertEqual(self.cycle(instance), 'incomplete')
        self.assertEqual(instance.problems, {'window_ignored': 1})
        self.assertEqual(self.logs.flow_ids(), [])

    def test_stopping_before_the_stated_total_is_incomplete(self):
        self.console.rows = self.recent(250)
        self.console.understate_pages = True
        instance = self.collector()
        self.assertEqual(self.cycle(instance), 'incomplete')
        self.assertEqual(instance.problems, {'invalid_pagination': 1})
        self.assertLess(instance.watermark, self.edge())

    def test_unusable_row_is_incomplete_not_a_crash(self):
        bad = row(1, self.edge() - 1000)
        self.console.rows = [bad]
        bad['protocol'] = {'unexpected': True}
        bad['source'] = 'not-an-object'
        instance = self.collector()
        self.assertEqual(self.cycle(instance), 'incomplete')
        self.assertEqual(instance.problems, {'unexpected_row': 1})
        self.assertLess(instance.watermark, self.edge())

    def test_changing_total_holds_the_watermark_until_a_stable_read(self):
        self.config.initial_lookback_s = 300
        rows = list(self.recent(250, age_s=200))
        self.console.rows = rows
        extra = row(9999, self.edge() - 500)
        self.console.insert_during_page = (1, extra)
        originals = {r['id'] for r in rows}
        instance = self.collector()
        self.assertEqual(self.cycle(instance), 'incomplete')
        self.assertEqual(instance.problems, {'changing_total': 1})
        self.assertLess(instance.watermark, self.edge())
        self.assertEqual(instance.counters['windows_shifted'], 1)
        self.now += 300
        self.assertEqual(self.cycle(instance), 'ok')
        self.assertTrue(originals <= set(self.logs.flow_ids()))
        self.assertEqual(self.logs.flow_ids().count(extra['id']), 1)
        self.assertEqual(len(self.logs.flow_ids()), len(set(self.logs.flow_ids())))

    def test_late_row_inside_the_overlap_is_collected_once(self):
        self.console.rows = self.recent(5)
        instance = self.collector()
        self.cycle(instance)
        first = instance.watermark
        self.now += 300
        late = row(5000, first - 60_000)
        self.console.rows.append(late)
        self.assertEqual(self.cycle(instance), 'ok')
        self.assertEqual(self.logs.flow_ids().count(late['id']), 1)
        self.assertEqual(instance.counters['rows_late'], 1)
        self.assertEqual(len(self.logs.flow_ids()), len(set(self.logs.flow_ids())))
        self.assertGreater(instance.counters['rows_duplicate'], 0)

    def test_page_budget_leaves_a_backlog_that_later_cycles_finish(self):
        self.config.initial_lookback_s = 3000
        self.config.max_pages_per_cycle = 1
        self.console.rows = [row(i + 1, self.edge() - 2_900_000 + i * 190) for i in range(15000)]
        instance = self.collector()
        results = [self.cycle(instance) for _ in range(4)]
        self.assertEqual(results[0], 'backlog', 'a window always fits, so a low budget slows catch-up but cannot stall it')
        self.assertIn('ok', results)
        self.assertEqual(len(set(self.logs.flow_ids())), 15000)


class Failures(Harness):
    def test_rejected_login_makes_no_flow_read_and_backs_off(self):
        self.console.login_status = 401
        instance = self.collector()
        self.assertEqual(self.cycle(instance), 'auth_failed')
        self.assertEqual((self.console.logins, len(self.console.flow_requests)), (1, 0))
        self.assertEqual(self.cycle(instance), 'auth_failed')
        self.assertEqual(self.console.logins, 1, 'no second login inside the backoff')
        self.now += self.config.poll_s
        self.assertEqual(self.cycle(instance), 'auth_failed')
        self.assertEqual(self.console.logins, 2)
        self.now += self.config.poll_s
        self.assertEqual(self.cycle(instance), 'auth_failed')
        self.assertEqual(self.console.logins, 2, 'the backoff doubles')
        self.assertIn('unifi_flows_logins_total{result="failed"} 2', instance.metrics())

    def test_missing_password_file_is_an_auth_failure(self):
        Path(self.config.password_file).unlink()
        self.assertEqual(self.cycle(self.collector()), 'auth_failed')
        self.assertEqual(self.console.logins, 0)

    def test_ended_session_logs_in_once_and_finishes_the_cycle(self):
        self.console.rows = self.recent(250)
        instance = self.collector()
        self.cycle(instance)
        self.now += 300
        self.console.rows += [row(7000 + i, self.edge() - 1000 - i * 2500) for i in range(150)]
        self.console.expire_session_at_page = 1
        logins = self.console.logins
        self.assertEqual(self.cycle(instance), 'ok')
        self.assertEqual(self.console.logins, logins + 1)
        self.assertEqual(len(self.logs.flow_ids()), len(set(self.logs.flow_ids())))
        self.assertEqual(len(self.logs.flow_ids()), 400)

    def test_ended_session_refused_again_is_access_denied_and_backs_off(self):
        self.console.rows = self.recent(5)
        instance = self.collector()
        self.cycle(instance)
        self.now += 300
        self.console.rows += self.recent(5)
        self.console.refuse_flows = 401
        logins = self.console.logins
        self.assertEqual(self.cycle(instance), 'access_denied')
        self.assertEqual(self.console.logins, logins + 1, 'one new login, not a loop')
        self.assertEqual(self.cycle(instance), 'access_denied', 'the backoff holds')
        self.assertEqual(self.console.logins, logins + 1)

    def test_refused_read_on_a_fresh_session_is_access_denied(self):
        self.console.refuse_flows = 403
        self.console.rows = self.recent(5)
        instance = self.collector()
        self.assertEqual(self.cycle(instance), 'access_denied')
        logins = self.console.logins
        self.assertEqual(self.cycle(instance), 'access_denied', 'the backoff keeps the cause')
        self.assertEqual(self.console.logins, logins)

    def test_failed_page_keeps_the_watermark_and_recovers(self):
        self.console.rows = self.recent(250)
        self.console.fail_page = 1
        instance = self.collector()
        self.assertEqual(self.cycle(instance), 'read_failed')
        self.assertLess(instance.watermark, self.edge())
        self.now += 300
        self.assertEqual(self.cycle(instance), 'ok')
        self.assertEqual(sorted(self.logs.flow_ids()), sorted(r['id'] for r in self.console.rows))

    def test_redirect_is_refused(self):
        self.console.rows = self.recent(5)
        self.console.fail_page = 0
        self.console.fail_status = 302
        self.assertEqual(self.cycle(self.collector()), 'read_failed')

    def test_rate_limit_is_honored_without_a_request(self):
        self.console.rows = self.recent(5)
        self.console.fail_page = 0
        self.console.fail_status = 429
        self.console.retry_after = 900
        instance = self.collector()
        self.assertEqual(self.cycle(instance), 'rate_limited')
        requests = len(self.console.flow_requests)
        self.now += 300
        self.assertEqual(self.cycle(instance), 'rate_limited')
        self.assertEqual(len(self.console.flow_requests), requests)
        self.now += 601
        self.assertEqual(self.cycle(instance), 'ok')

    def test_rejected_ingest_keeps_the_watermark_and_resends(self):
        self.console.rows = self.recent(50)
        instance = self.collector()
        self.logs.fail_writes = 2
        self.assertEqual(self.cycle(instance), 'ingest_failed')
        self.assertLess(instance.watermark, self.edge())
        self.assertEqual(self.logs.flow_ids(), [])
        self.assertIn('unifi_flows_ingest_failures_total 1', instance.metrics())
        self.now += 300
        self.assertEqual(self.cycle(instance), 'ok')
        self.assertEqual(sorted(self.logs.flow_ids()), sorted(r['id'] for r in self.console.rows))


class Recovery(Harness):
    def test_restart_resumes_from_the_checkpoint_within_one_overlap(self):
        self.console.rows = self.recent(10)
        first = self.collector()
        self.cycle(first)
        watermark = first.watermark
        self.now += 1800
        newer = [row(3000 + i, watermark + 10_000 + i * 1000) for i in range(40)]
        self.console.rows += newer
        second = self.collector()
        self.assertEqual(self.cycle(second), 'ok')
        self.assertGreater(second.watermark, watermark)
        ids = self.logs.flow_ids()
        self.assertTrue({r['id'] for r in newer} <= set(ids))
        repeated = len(ids) - len(set(ids))
        inside_overlap = sum(1 for r in self.console.rows if watermark - self.config.overlap_s * 1000 <= r['time'] <= watermark)
        self.assertEqual(repeated, inside_overlap, 'only the overlap repeats, and flow.id still identifies it')

    def test_first_run_starts_at_the_initial_lookback(self):
        self.console.rows = [row(1, self.edge() - (self.config.initial_lookback_s + 600) * 1000), row(2, self.edge() - 1000)]
        instance = self.collector()
        self.cycle(instance)
        self.assertEqual(self.logs.flow_ids(), [f'{2:024x}'])

    def test_missing_state_store_stops_before_touching_the_console(self):
        self.logs.fail_reads = True
        instance = self.collector()
        self.assertEqual(self.cycle(instance), 'state_unavailable')
        self.assertEqual((self.console.logins, len(self.console.flow_requests)), (0, 0))
        self.logs.fail_reads = False
        self.assertEqual(self.cycle(instance), 'no_rows')

    def test_outage_beyond_catch_up_is_a_recorded_gap(self):
        first = self.collector()
        self.cycle(first)
        watermark = first.watermark
        self.now += self.config.max_catchup_s + 3600
        second = self.collector()
        self.config.max_pages_per_cycle = 500
        self.config.max_cycle_s = 100000
        self.assertEqual(self.cycle(second), 'no_rows')
        gap = self.logs.of_type('gap')[0]
        self.assertEqual(gap['gap.reason'], 'beyond_catchup')
        self.assertEqual(gap['gap.start'], collector.iso(watermark))
        self.assertAlmostEqual(gap['gap.seconds'], 3600, delta=self.config.settle_s + self.config.initial_lookback_s)
        self.assertIn('unifi_flows_gap_seconds_total', second.metrics())

    def test_gap_record_survives_a_failed_write(self):
        first = self.collector()
        self.cycle(first)
        self.now += self.config.max_catchup_s + 3600
        second = self.collector()
        self.config.max_pages_per_cycle = 500
        self.config.max_cycle_s = 100000
        with contextlib.redirect_stdout(self.out):
            second.restore()
        self.logs.fail_writes = 1
        self.assertEqual(self.cycle(second), 'ingest_failed')
        self.assertEqual(self.logs.of_type('gap'), [])
        self.now += 300
        self.cycle(second)
        self.assertEqual(len(self.logs.of_type('gap')), 1)


class Privacy(Harness):
    def test_nothing_sensitive_reaches_logs_or_metrics(self):
        self.console.rows = self.recent(150)
        instance = self.collector()
        self.cycle(instance)
        self.now += 300
        self.console.expire_session_at_page = 0
        self.cycle(instance)
        self.now += 300
        self.console.fail_page = 0
        self.cycle(instance)
        self.now += 300
        self.cycle(instance)
        text = self.out.getvalue() + instance.metrics()
        for secret in (PASSWORD, 'CANARY-CSRF', 'CANARY-COOKIE', '192.0.2.', '198.51.100.', '02:00:00', 'Synthetic', 'synthetic'):
            self.assertNotIn(secret, text)

    def test_stored_record_keeps_only_named_fields(self):
        record = collector.flow_record(TEMPLATES[0])
        self.assertEqual(record['_time'], '2026-08-01T00:00:00.000Z')
        self.assertEqual((record['source.network'], record['destination.network'], record['flow.scope']), ('Default', 'IoT', 'inter_network'))
        self.assertEqual((record['source.address'], record['destination.port'], record['network.transport']), ('192.0.2.10', 80, 'tcp'))
        self.assertEqual((record['network.bytes'], record['flow.policies']), (1200, 'CONNTRACK'))
        self.assertNotIn('02:00', json.dumps(record))
        self.assertNotIn('client_fingerprint', json.dumps(record))

    def test_scopes_cover_each_destination(self):
        by_id = {r['id']: collector.flow_record(r)['flow.scope'] for r in TEMPLATES}
        self.assertEqual(sorted(by_id.values()), ['inter_network', 'inter_network', 'inter_network', 'internet'])
        gateway = copy.deepcopy(TEMPLATES[0])
        gateway['destination']['zone_name'] = 'Gateway'
        gateway['out'] = None
        self.assertEqual(collector.flow_record(gateway)['flow.scope'], 'gateway')
        same = copy.deepcopy(TEMPLATES[0])
        same['out'] = same['in']
        self.assertEqual(collector.flow_record(same)['flow.scope'], 'same_network')

    def test_row_without_an_id_gets_a_stable_derived_one(self):
        bare = copy.deepcopy(TEMPLATES[0])
        del bare['id']
        self.assertEqual(collector.flow_id(bare), collector.flow_id(copy.deepcopy(bare)))
        self.assertTrue(collector.flow_id(bare).startswith('derived-'))

    def test_console_verifies_tls(self):
        tls = collector.Console(self.config).tls
        self.assertEqual((tls.verify_mode, tls.check_hostname), (ssl.CERT_REQUIRED, True))


if __name__ == '__main__':
    unittest.main(verbosity=1)
