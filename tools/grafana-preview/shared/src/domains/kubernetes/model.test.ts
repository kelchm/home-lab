import { readFileSync } from 'node:fs';
import type { TimeRange } from '@grafana/data';
import { describe, expect, it } from 'vitest';
import type { DomainContext, DomainSection } from '../contract';
import type { FrameLike } from '../../model/semantics';
import { domain } from './index';
import {
  at, attention, backupState, certificates, CNPG_STALE_SECONDS, controllers, databases, fluxCounts, fluxResources,
  longhornSummary, namespaces, queries, ref, resourceIdentity, resourceKey, services, volumes,
} from './model';
import evidence from './evidence.json';

interface Captured { labels: Record<string, string>; t: number[]; v: Array<number | null> }
interface Entry { expr: string; series: Captured[] }
const root = new URL('../../../../', import.meta.url);
const bounded: { from: number; to: number; instant: Record<string, Entry>; range: Record<string, Entry> } = JSON.parse(readFileSync(new URL('fixtures/bounded.json', root), 'utf8'));
const catalog: Array<Entry & { refId: string }> = JSON.parse(readFileSync(new URL('runtime/catalog.json', root), 'utf8'));
const FROM = bounded.from; const TO = bounded.to;
const frame = (key: string, values: Array<number | null>, labels: Record<string, string> = {}, times = values.map((_, i) => TO - (values.length - i - 1) * 60000)): FrameLike => ({
  refId: ref(key), fields: [{ name: 'Time', type: 'time', values: times, config: { interval: 60000 } }, { name: 'Value', type: 'number', labels, values }],
});
const scalar = (key: string, value: number, labels: Record<string, string> = {}) => frame(key, [value], labels);
function ctx(series: FrameLike[] = [], extra: Partial<DomainContext> = {}): DomainContext {
  const timeRange = { from: { valueOf: () => FROM }, to: { valueOf: () => TO }, raw: { from: FROM, to: TO } } as unknown as TimeRange;
  const url = (path: string, extra: Record<string, string>) => `http://preview.test/${path}?${new URLSearchParams({ from: String(FROM), to: String(TO), 'var-source': 'replay', ...extra })}`;
  return {
    series, timeRange, timeZone: 'UTC', entity: null, mode: 'recorded',
    coverage: { freshness: 'fresh', missing: 0, retained: 0, retainedAt: null, unanswered: false },
    href: (route) => url('preview', Object.fromEntries(Object.entries({ focus: 'Kubernetes', ...route }).filter(([, v]) => v != null).map(([k, v]) => [k, String(v)]))),
    deeper: (uid, variables) => url(`d/${uid}`, Object.fromEntries(Object.entries(variables ?? {}).map(([k, v]) => [`var-${k}`, v]))), ...extra,
  };
}
const page = (id: string, context: DomainContext) => domain.pages.find((item) => item.id === id)!.sections(context);
const source = [scalar('ksmUp', 1), scalar('ksmAge', 20)];
const longhorn = [scalar('longhornCoverage', 100), scalar('longhornAge', 20)];
const controller = { namespace: 'iot', kind: 'Deployments', controller: 'broadsheet' };
const cframe = (key: string, value: number) => scalar(key, value, controller);
const group = (total: number, ready: number, missing = 0) => [scalar('controllerTotal', total, { kind: 'Deployments' }), scalar('controllerReady', ready, { kind: 'Deployments' }), scalar('controllerMissing', missing, { kind: 'Deployments' })];
function replayFrames() {
  return queries.flatMap((query) => {
    if (!query.capture) return [];
    const capture = query.capture;
    const entry = capture.kind === 'bounded' ? bounded[capture.range ? 'range' : 'instant'][capture.key] : catalog.find((entry) => entry.refId === capture.key);
    if (!entry) throw new Error(`Capture missing: ${query.refId}`);
    return entry.series.map((row) => frame(query.key, row.v, row.labels, row.t));
  });
}
const replay = replayFrames();
const textOf = (sections: DomainSection[]) => JSON.stringify(sections);
function linksOf(sections: DomainSection[]): string[] {
  return sections.flatMap((section) => section.kind === 'table' ? section.rows.flatMap((row) => row.cells.flatMap((cell) => typeof cell === 'string' || !cell.href ? [] : [cell.href])) : section.kind === 'links' ? section.items.map((item) => item.href) : section.kind === 'text' ? section.links?.map((item) => item.href) ?? [] : section.kind === 'metrics' ? section.items.flatMap((item) => item.href ? [item.href] : []) : []);
}

describe('Kubernetes domain replay and query contract', () => {
  it('exports a complete domain with unique query and section identities', () => {
    expect(domain.id).toBe('k8s'); expect(domain.focus).toBe('Kubernetes');
    expect(new Set(queries.map((query) => query.refId)).size).toBe(queries.length);
    for (const query of queries) {
      expect(query.refId).toBe(ref(query.key)); expect(query.scope).toBe('k8s');
      expect(query.instant).not.toBe(query.range); expect(query).not.toHaveProperty('series');
      if (!query.capture) expect(query.optional).toBe(true);
    }
    for (const destination of domain.pages) for (const mode of ['recorded', 'live'] as const) {
      const sections = destination.sections(ctx(replay, { mode }));
      expect(new Set(sections.map((section) => section.id)).size).toBe(sections.length);
      for (const required of destination.required) expect(queries.some((query) => query.refId === required)).toBe(true);
      for (const section of sections) if (section.kind === 'chart') {
        expect(section.refs.length).toBeGreaterThan(0);
        for (const key of section.refs) expect(queries.some((query) => query.refId === key && query.range)).toBe(true);
      }
    }
  });

  it('resolves captures and records deliberate changes to captured live expressions', () => {
    for (const query of queries.filter((query) => query.capture)) {
      const capture = query.capture!;
      const entry = capture.kind === 'bounded' ? bounded[capture.range ? 'range' : 'instant'][capture.key] : catalog.find((entry) => entry.refId === capture.key)!;
      expect(entry).toBeDefined();
      if (query.expr !== entry.expr) expect(evidence.capturedExpressionsBeforeChanges).toHaveProperty(query.key, entry.expr);
    }
    expect(queries.some((query) => query.expr.includes('cnpg_collector_last_available_backup_timestamp'))).toBe(false);
    expect(queries.find((query) => query.key === 'dbBackupTimestamp')?.expr).toContain('barman_cloud_cloudnative_pg_io_last_available_backup_timestamp');
    expect(queries.find((query) => query.key === 'dbExpected')?.expr).toContain('kube_pod_init_container_info');
    expect(queries.find((query) => query.key === 'dbExpected')?.expr).toContain('or count by (namespace, pod) (cnpg_collector_up');
    expect(queries.find((query) => query.key === 'routeErrors')?.expr).not.toMatch(/vector\(0\)|\* 0/);
  });

  it('uses named captured controllers and namespaces instead of a totals-only page', () => {
    const rows = controllers(ctx(replay));
    expect(rows).toHaveLength(92);
    expect(rows.every((row) => row.state === 'meets-desired' || row.state === 'scaled-to-zero')).toBe(true);
    expect(rows.find((row) => row.name === 'broadsheet')).toMatchObject({ namespace: 'iot', desired: 1, available: 1 });
    expect(namespaces(ctx(replay)).find((row) => row.name === 'iot')).toMatchObject({ running: 4, restarts: 0 });
    expect(services(ctx(replay))).toHaveLength(13);
    expect(certificates(ctx(replay))).toHaveLength(9);
  });
  it('separates missing collection from an observed namespace with no named controller rows', () => {
    const namespace = scalar('nsPods', 1, { namespace: 'standalone-pods' });
    const cell = (frames: FrameLike[]) => {
      const table = page('workloads', ctx(frames)).find((section) => section.id === 'k8s-namespaces');
      if (table?.kind !== 'table') throw new Error('Missing namespace table');
      return table.rows[0].cells[5];
    };
    expect(cell([namespace])).toMatchObject({ text: 'Controller inventory unavailable', tone: 'unknown' });
    expect(cell([...source, namespace])).toMatchObject({ text: 'No named controller observations', tone: 'unknown', detail: expect.stringContaining('does not establish that none are configured') });
  });

  it('keeps current CNPG recovery unknown beside fully accounted Longhorn backup counts', () => {
    const context = ctx(replay);
    expect(longhornSummary(context)).toMatchObject({ expected: 37, recent: 37, accounted: true, target: 1 });
    expect(databases(context)).toHaveLength(4);
    for (const row of databases(context)) {
      expect(row.namespace).toBeNull(); expect(row.up).toBe(1); expect(row.expected).toBe(false);
      expect(row.size).toBeGreaterThan(0); expect(row.backup.state).toBe('unknown'); expect(row.wal).toBeNull();
    }
    expect(volumes(context)).toEqual([]);
    expect(textOf(page('storage', context))).toContain('Recovery evidence incomplete');
    expect(textOf(page('storage', context))).toContain('Named volume inventory was not captured');
    expect(textOf(page('storage', context))).not.toContain('Protection complete');
  });

  it('leaves uncaptured route latency, errors and histories unavailable in recorded mode', () => {
    const context = ctx(replay);
    expect(services(context).every((row) => row.errors == null && row.p95 == null)).toBe(true);
    for (const key of ['routeP95', 'routeErrors', 'dbExpected', 'dbBackupTimestamp', 'routeTrafficHistory', 'cpuCommitHistory']) {
      expect(queries.find((query) => query.key === key)?.capture).toBeUndefined();
      expect(at(context, key)).toBeNull();
    }
  });
});

describe('Kubernetes semantic failure cases', () => {
  it('never converts missing data or unknown entities to zero or healthy', () => {
    const context = ctx();
    expect(controllers(context)).toEqual([]); expect(databases(context)).toEqual([]);
    expect(longhornSummary(context)).toMatchObject({ observed: false, accounted: false, expected: null });
    const missing = page('resource', { ...context, entity: resourceKey('namespace', 'missing') });
    expect(textOf(missing)).toContain('Resource observations unavailable');
    expect(missing.some((section) => section.kind === 'table' || section.kind === 'metrics')).toBe(false);
  });

  it('requires complete matching named inventory before inferring individual availability from aggregate counts', () => {
    const data = [...source, ...group(2, 2), cframe('controllerInventory', 1)];
    expect(controllers(ctx(data))[0]).toMatchObject({ desired: 1, available: null, state: 'unknown' });
    expect(controllers(ctx([...source, ...group(1, 1), cframe('controllerInventory', 1)]))[0]).toMatchObject({ available: 1, state: 'meets-desired' });
  });

  it.each([0, 91])('masks availability when the source fails or ages out (%s)', (sample) => {
    const data = [scalar('ksmUp', sample === 0 ? 0 : 1), scalar('ksmAge', sample), ...group(1, 1), cframe('controllerInventory', 1), cframe('controllerAvailable', 1)];
    expect(controllers(ctx(data))[0]).toMatchObject({ state: 'unknown', available: null });
  });

  it('uses a named deficit to identify the affected controller and next action', () => {
    const context = ctx([...source, cframe('controllerInventory', 3), cframe('controllerDeficit', 2)]);
    expect(controllers(context)[0]).toMatchObject({ desired: 3, available: 1, state: 'below-desired' });
    expect(attention(context)[0]).toMatchObject({ resource: 'iot/broadsheet', state: 'warning' });
    const detail = textOf(page('resource', { ...context, entity: controllers(context)[0].key }));
    expect(detail).toContain('Inspect namespace and pods'); expect(detail).toContain('var-namespace=iot');
  });

  it('explicit missing status and ambiguous duplicate series defeat a ready aggregate', () => {
    const data = [...source, ...group(1, 1), cframe('controllerInventory', 1)];
    expect(controllers(ctx([...data, cframe('controllerUnknown', 1)]))[0].state).toBe('unknown');
    expect(controllers(ctx([...data, cframe('controllerAvailable', 1), cframe('controllerAvailable', 0)]))[0].state).toBe('unknown');
    expect(controllers(ctx([...source, cframe('controllerInventory', 1), cframe('controllerInventory', 2), cframe('controllerAvailable', 1)]))[0].state).toBe('unknown');
  });

  it('a configured zero desired count describes configuration rather than service readiness', () => {
    const row = controllers(ctx([...source, cframe('controllerInventory', 0), cframe('controllerAvailable', 0)]))[0];
    expect(row.state).toBe('scaled-to-zero');
    expect(textOf(controllerDetail(row.key, [...source, cframe('controllerInventory', 0), cframe('controllerAvailable', 0)]))).toContain('Configured with zero replicas');
  });

  it('preserves namespace identity and does not assign missing namespace counters a zero', () => {
    const context = ctx([...source, cframe('controllerInventory', 1), scalar('nsPods', 2, { namespace: 'iot' }), scalar('nsRestarts', 0, { namespace: 'media' })]);
    expect(namespaces(context).find((row) => row.name === 'iot')?.restarts).toBeNull();
    expect(namespaces(context).find((row) => row.name === 'media')?.running).toBeNull();
  });

  it('old selected-window samples cannot be read as current facts in a different range', () => {
    const timeRange = { from: { valueOf: () => TO + 60000 }, to: { valueOf: () => TO + 3600000 } } as unknown as TimeRange;
    const context = ctx(replay, { timeRange });
    expect(controllers(context).every((row) => row.available == null)).toBe(true);
    expect(services(context).every((row) => row.perMinute == null)).toBe(true);
    expect(longhornSummary(context).accounted).toBe(false);
  });

  it('does not infer zero errors from fresh request traffic or latency from a zero-traffic route', () => {
    const labels = { svc: 'observability-grafana' };
    expect(services(ctx([scalar('routeRate', 10, labels)]))[0]).toMatchObject({ errors: null, p95: null });
    expect(services(ctx([scalar('routeRate', 0, labels), scalar('routeP95', 0, labels), scalar('routeErrors', 0, labels)]))[0]).toMatchObject({ perMinute: 0, errors: null, p95: null });
  });

  it('Barman distinguishes zero, missing, future and stale timestamps using the selected end time', () => {
    expect(backupState(0, TO, CNPG_STALE_SECONDS).state).toBe('never');
    expect(backupState(null, TO, CNPG_STALE_SECONDS).state).toBe('unknown');
    expect(backupState(TO / 1000 + 1, TO, CNPG_STALE_SECONDS).state).toBe('unknown');
    expect(backupState(TO / 1000 - CNPG_STALE_SECONDS - 1, TO, CNPG_STALE_SECONDS).state).toBe('stale');
    expect(backupState(TO / 1000 - 3600, TO, CNPG_STALE_SECONDS)).toMatchObject({ state: 'recent', age: 3600 });
  });

  it('retains expected database pods when their collector or backup metrics disappear', () => {
    const labels = { namespace: 'printing', pod: 'bambuddy-db-1' };
    let context = ctx([scalar('dbExpected', 1, labels)]);
    expect(databases(context)[0]).toMatchObject({ pod: labels.pod, expected: true, up: null, backup: { state: 'unknown' }, wal: null });
    context = ctx([scalar('dbExpected', 1, labels), scalar('dbUp', 0, labels), scalar('dbBackupTimestamp', TO / 1000 - 1000, labels), scalar('dbWal', 0, labels)]);
    expect(databases(context)[0]).toMatchObject({ expected: true, up: 0, backup: { state: 'unknown' }, wal: null });
    expect(attention(context)[0].finding).toContain('collector reports unavailable');
  });

  it('does not merge same-named database pods in different namespaces or attach pod-only legacy data', () => {
    const a = { namespace: 'a', pod: 'database-1' }; const b = { namespace: 'b', pod: 'database-1' };
    const context = ctx([scalar('dbExpected', 1, a), scalar('dbExpected', 1, b), scalar('dbUp', 1, { pod: 'database-1' }), scalar('dbSize', 123, { pod: 'database-1' })]);
    const rows = databases(context);
    expect(rows).toHaveLength(3);
    expect(rows.filter((row) => row.namespace).every((row) => row.size == null && row.up == null)).toBe(true);
    expect(rows.find((row) => row.namespace == null)?.size).toBe(123);
  });

  it('shows WAL backlog and stale base backups independently without inventing the alert duration', () => {
    const labels = { namespace: 'printing', pod: 'bambuddy-db-1' };
    const context = ctx([scalar('dbExpected', 1, labels), scalar('dbUp', 1, labels), scalar('dbBackupTimestamp', TO / 1000 - 27 * 3600, labels), scalar('dbWal', 4, labels)]);
    expect(attention(context).map((row) => row.finding)).toEqual(['Base backup older than the existing 26h rule', '4 WAL segments waiting to archive']);
    expect(textOf(page('storage', context))).toContain('alert duration is not established');
  });

  it('requires Longhorn inventory, collector freshness and balanced outcome counts', () => {
    const counts = [scalar('backupExpected', 2), scalar('backupRecent', 2), scalar('backupStale', 0), scalar('backupNever', 0), scalar('backupMissing', 0)];
    expect(longhornSummary(ctx(counts)).accounted).toBe(false);
    expect(longhornSummary(ctx([...longhorn, ...counts])).accounted).toBe(true);
    expect(longhornSummary(ctx([...longhorn, ...counts.filter((row) => row.refId !== ref('backupMissing')), scalar('backupMissing', 1)])).accounted).toBe(false);
    expect(longhornSummary(ctx([...longhorn, scalar('backupExpected', 0), scalar('backupRecent', 0), scalar('backupStale', 0), scalar('backupNever', 0), scalar('backupMissing', 0)])).accounted).toBe(false);
  });
  it.each([-1, TO / 1000 + 3600])('rejects aggregate reassurance contradicted by a named invalid timestamp %s', (timestamp) => {
    const labels = { volume: 'pvc-clock', pvc_namespace: 'iot', pvc: 'clock' };
    const counts = [scalar('backupExpected', 1), scalar('backupRecent', 1), scalar('backupStale', 0), scalar('backupNever', 0), scalar('backupMissing', 0)];
    const context = ctx([...longhorn, ...counts, scalar('volumeBackupTimestamp', timestamp, labels)]);
    expect(volumes(context)[0].backup.state).toBe('unknown');
    expect(longhornSummary(context).accounted).toBe(false);
    expect(textOf(page('overview', context))).not.toContain('1 / 1 within 30h');
    expect(textOf(page('overview', context))).toContain('Coverage / accounting incomplete');
  });
  it('keeps exempt invalid timestamps outside the protected-volume aggregate', () => {
    const labels = { volume: 'pvc-cache', pvc_namespace: 'iot', pvc: 'cache' };
    const counts = [scalar('backupExpected', 1), scalar('backupRecent', 1), scalar('backupStale', 0), scalar('backupNever', 0), scalar('backupMissing', 0)];
    expect(longhornSummary(ctx([...longhorn, ...counts, scalar('volumeBackupTimestamp', TO / 1000 + 3600, labels), scalar('volumeExempt', 1, labels)])).accounted).toBe(true);
  });

  it('a named volume exception remains actionable even when full per-volume inventory was not captured', () => {
    const labels = { volume: 'pvc-123', pvc_namespace: 'iot', pvc: 'broadsheet', problem: 'stale' };
    const context = ctx([...longhorn, scalar('backupExceptions', 110000, labels)]);
    expect(volumes(context)[0]).toMatchObject({ pvc: 'broadsheet', capacity: null, robustness: null, backup: { state: 'stale' } });
    expect(attention(context)[0]).toMatchObject({ resource: 'iot/broadsheet', finding: 'Volume backup stale' });
  });

  it('independent volume evidence cannot turn missing backup or robustness into healthy', () => {
    const labels = { volume: 'pvc-123', pvc_namespace: 'iot', pvc: 'broadsheet' };
    const context = ctx([...longhorn, scalar('volumeInventory', 1024 ** 3, labels), scalar('volHealthy', 34)]);
    expect(volumes(context)[0]).toMatchObject({ robustness: null, backup: { state: 'unknown' } });
    const exempt = volumes(ctx([...context.series, scalar('volumeExempt', 1, { pvc_namespace: 'iot', pvc: 'broadsheet' })]))[0];
    expect(exempt.backup.state).toBe('exempt'); expect(exempt.robustness).toBeNull();
  });

  it('ambiguous state flags and duplicate timestamps remain unknown', () => {
    const labels = { volume: 'pvc-123', pvc_namespace: 'iot', pvc: 'broadsheet' };
    const rows = volumes(ctx([...longhorn, scalar('volumeInventory', 1, labels), scalar('volumeState', 1, { ...labels, state: 'healthy' }), scalar('volumeState', 1, { ...labels, state: 'faulted' }), scalar('volumeBackupTimestamp', TO / 1000 - 100, labels), scalar('volumeBackupTimestamp', TO / 1000 - 200, labels)]));
    expect(rows[0]).toMatchObject({ robustness: null, backup: { state: 'unknown' } });
  });

  it('certificate names in different namespaces never overwrite each other and expiry adds no arbitrary warning threshold', () => {
    const context = ctx([scalar('certLeft', 1000, { namespace: 'a', name: 'web' }), scalar('certLeft', -10, { namespace: 'b', name: 'web' })]);
    expect(certificates(context)).toHaveLength(2);
    expect(attention(context)).toHaveLength(1);
    expect(resourceIdentity(attention(context)[0].key)?.parts).toEqual(['b', 'web']);
  });

  it('keeps Flux suspension distinct from an active reconciliation failure', () => {
    const a = { namespace: 'iot', name: 'broadsheet', kind: 'HelmRelease' };
    const context = ctx([scalar('fluxInventory', 1, { ...a, ready: 'False', suspended: 'True' })]);
    expect(fluxResources(context)[0]).toMatchObject({ ready: 'False', suspended: 'True' });
    expect(attention(context)).toEqual([]);
    expect(attention(ctx([scalar('fluxInventory', 1, { ...a, ready: 'False', suspended: 'False' })]))[0].finding).toBe('Flux ready=False');
  });
  it('keeps raw or unbound Longhorn volumes in the observed policy set', () => {
    const labels = { volume: 'raw-volume' };
    const data = ctx([...longhorn, scalar('volumeInventory', 1, labels), scalar('volumeBackupTimestamp', 0, labels), scalar('volumeExempt', 1, { pvc_namespace: 'iot', pvc: 'cache' })]);
    expect(volumes(data)[0]).toMatchObject({ volume: 'raw-volume', namespace: null, pvc: null, backup: { state: 'never' } });
    expect(evidence.backupScope.sources).toContain('kubernetes/apps/longhorn-system/longhorn/app/alerts.yaml');
  });
  it('distinguishes unsupported Flux fallback zeros from an observed zero count', () => {
    const zeros = [scalar('fluxFailing', 0), scalar('fluxSuspended', 0)];
    expect(fluxCounts(ctx(zeros))).toEqual({ ready: null, failing: null, suspended: null });
    expect(fluxCounts(ctx([]))).toEqual({ ready: null, failing: null, suspended: null });
    expect(fluxCounts(ctx([...zeros, scalar('fluxReady', 3)]))).toEqual({ ready: 3, failing: 0, suspended: 0 });
    expect(fluxCounts(ctx([scalar('fluxReady', 3)]))).toEqual({ ready: 3, failing: null, suspended: null });
    const counts = page('workloads', ctx(zeros)).find((section) => section.id === 'k8s-flux-counts');
    expect(counts?.kind === 'text' && counts.text).toContain('Unknown reported failing');
    const labels = { namespace: 'iot', name: 'broadsheet', kind: 'HelmRelease', ready: 'True', suspended: 'False' };
    expect(fluxCounts(ctx([...zeros, scalar('fluxInventory', 1, labels)]))).toEqual({ ready: null, failing: 0, suspended: 0 });
  });
  it('uses source-dependent zero fallbacks for live Flux and Longhorn totals', () => {
    for (const key of ['fluxFailing', 'fluxSuspended', 'volHealthy', 'volDegraded', 'volFaulted']) {
      const query = queries.find((query) => query.key === key)!;
      expect(query.expr).not.toContain('vector(0)');
      expect(query.expr).toContain(`0 * count(${key.startsWith('flux') ? 'flux_resource_info' : 'longhorn_volume_robustness'}{cluster="k8s-prod"})`);
    }
    const rows = volumes(ctx([scalar('volHealthy', 0), scalar('volDegraded', 0), scalar('volFaulted', 0), scalar('volumeInventory', 1, { volume: 'one' })]));
    expect(rows[0]).toMatchObject({ robustness: null, backup: { state: 'unknown' } });
  });
});

function controllerDetail(entity: string, series: FrameLike[]) { return page('resource', ctx(series, { entity })); }

describe('Kubernetes navigation and entity isolation', () => {
  it('preserves time, source and exact entity through the workload and native-detail journey', () => {
    const context = ctx(replay);
    const row = controllers(context).find((row) => row.name === 'broadsheet')!;
    const link = linksOf(page('workloads', context)).map((href) => new URL(href)).find((url) => url.searchParams.get('entity') === row.key)!;
    expect(link.searchParams.get('from')).toBe(String(FROM)); expect(link.searchParams.get('to')).toBe(String(TO));
    expect(link.searchParams.get('var-source')).toBe('replay'); expect(link.searchParams.get('focus')).toBe('Kubernetes');
    const detail = page('resource', { ...context, entity: row.key });
    const table = detail.find((section) => section.kind === 'table')!;
    expect(table.kind === 'table' && table.rows).toHaveLength(1);
    const native = linksOf(detail).map((href) => new URL(href)).find((url) => url.pathname.includes('/d/a87fb0d919ec0ea5f6543124e16c42a5'))!;
    expect(native.searchParams.get('var-namespace')).toBe('iot'); expect(native.searchParams.get('var-source')).toBe('replay');
    expect(native.searchParams.get('from')).toBe(String(FROM)); expect(native.searchParams.get('to')).toBe(String(TO));
  });

  it('database detail links preserve the pod while leaving uncaptured namespace unset', () => {
    const row = databases(ctx(replay))[0];
    const native = linksOf(page('resource', ctx(replay, { entity: row.key }))).map((href) => new URL(href)).find((url) => url.pathname.includes('/d/cloudnative-pg'))!;
    expect(native.searchParams.get('var-instances')).toBe(row.pod);
    expect(native.searchParams.has('var-namespace')).toBe(false);
    expect(native.searchParams.get('var-source')).toBe('replay');
  });

  it('native route filters match the original exporter labels and preserve regex characters literally', () => {
    const name = 'iot-broadsheet';
    const sections = page('resource', ctx([scalar('routeRate', 1, { svc: name })], { entity: resourceKey('service', name) }));
    const url = linksOf(sections).map((href) => new URL(href)).find((url) => url.pathname.includes('/d/n5bu_kv4k'))!;
    const filter = new RegExp(`^${url.searchParams.get('var-service')}$`);
    expect(filter.test('httproute-iot-broadsheet-gw-network-gateway-http@kubernetescrd')).toBe(true);
    expect(filter.test('iot-broadsheet@kubernetes')).toBe(true);
    expect(filter.test('iot-something-else@kubernetes')).toBe(false);
    const literal = 'route.with+regex';
    const other = linksOf(page('resource', ctx([scalar('routeRate', 1, { svc: literal })], { entity: resourceKey('service', literal) }))).map((href) => new URL(href)).find((url) => url.pathname.includes('/d/n5bu_kv4k'))!;
    const literalFilter = new RegExp(`^${other.searchParams.get('var-service')}$`);
    expect(literalFilter.test(literal)).toBe(true);
    expect(literalFilter.test('routexwithhhhregex')).toBe(false);
  });

  it('resource keys survive delimiter characters and an unknown key never falls back to aggregate values', () => {
    const key = resourceKey('service', 'a|b@c');
    expect(resourceIdentity(key)).toEqual({ kind: 'service', parts: ['a|b@c'] });
    expect(resourceIdentity('bad-json')).toBeNull();
    expect(textOf(page('resource', ctx(replay, { entity: resourceKey('database', 'missing', 'bambuddy-db-1') })))).toContain('Resource observations unavailable');
  });
});
