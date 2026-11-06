import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import type { TimeRange } from '@grafana/data';
import type { DomainContext, DomainSection } from '../contract';
import type { FrameLike } from '../../model/semantics';
import { domain } from './index';
import { backups, guests, hosts, pools, queries, ref } from './model';
import evidence from './evidence.json';

interface Captured { labels: Record<string, string>; t: number[]; v: Array<number | null> }
interface CaptureSource { expr: string; series: Captured[]; refId?: string }
const root = new URL('../../../../', import.meta.url);
const bounded: { from: number; to: number; instant: Record<string, CaptureSource>; range: Record<string, CaptureSource> } = JSON.parse(readFileSync(new URL('fixtures/bounded.json', root), 'utf8'));
const catalog: CaptureSource[] = JSON.parse(readFileSync(new URL('runtime/catalog.json', root), 'utf8'));
const TO = bounded.to;
const FROM = bounded.from;
const frame = (key: string, v: Array<number | null>, labels: Record<string, string> = {}, times = v.map((_, i) => TO - (v.length - 1 - i) * 60000)): FrameLike => ({ refId: ref(key), fields: [{ name: 'Time', type: 'time', values: times }, { name: 'Value', type: 'number', labels, values: v }] });
function ctx(series: FrameLike[], extra: Partial<DomainContext> = {}): DomainContext {
  const link = (path: string, params: Record<string, unknown>) => `${path}?${new URLSearchParams(Object.entries({ from: FROM, to: TO, 'var-source': 'replay', ...params }).filter(([, v]) => v != null).map(([k, v]) => [k, String(v)]))}`;
  return {
    series, entity: null, mode: 'live', timeZone: 'UTC', timeRange: { from: { valueOf: () => FROM }, to: { valueOf: () => TO }, raw: { from: FROM, to: TO } } as unknown as TimeRange,
    coverage: { freshness: 'fresh', missing: 0, retained: 0, retainedAt: null, unanswered: false },
    href: (route) => link('/preview', { focus: 'Proxmox', ...route }), deeper: (uid, variables) => link(`/d/${uid}`, Object.fromEntries(Object.entries(variables ?? {}).map(([k, v]) => [`var-${k}`, v]))), ...extra,
  };
}
const sections = (page: string, context: DomainContext) => domain.pages.find((item) => item.id === page)!.sections(context);
const text = (page: string, context: DomainContext) => JSON.stringify(sections(page, context));
const identity = { id: 'qemu/100', name: 'guest-a', node: 'pve-sbx-1', type: 'qemu' };
const guestInfo = () => frame('guestInfo', [1], identity);
const completion = (job: string, outcome: string, ageHours: number, node = 'pve-sbx-1') => frame('backupCompletion', [TO / 1000 - ageHours * 3600], { instance: node, backup_job: job, outcome });
const recorded = queries.flatMap((q) => {
  if (!q.capture) return [];
  const c = q.capture;
  const source = c.kind === 'bounded' ? bounded[c.range ? 'range' : 'instant'][c.key] : catalog.find((item) => item.refId === c.key);
  if (!source) throw new Error(`Capture missing: ${q.refId}`);
  return source.series.map((s) => frame(q.key, s.v, s.labels, s.t));
});

describe('Proxmox identity, unknown and zero semantics', () => {
  it('keeps expected nodes and completion gaps without manufacturing guests or successful jobs', () => {
    const empty = ctx([]);
    expect(guests(empty)).toEqual([]);
    expect(hosts(empty)).toHaveLength(3);
    expect(hosts(empty).every((host) => host.collection == null && host.cpu == null && host.apiUp == null)).toBe(true);
    expect(backups(empty)).toHaveLength(3);
    expect(backups(empty).every((job) => job.outcome === 'unknown' && job.dailyFreshness === 'unknown')).toBe(true);
    expect(text('overview', empty)).toContain('Unavailable');
    expect(text('backups', empty)).not.toContain('Clean completion within 30h');
  });
  it('does not treat a stopped guest or disabled boot policy as an incident or desired-state mismatch', () => {
    const data = ctx([guestInfo(), frame('guestUp', [0], identity), frame('guestOnboot', [0], identity), frame('guestCpu', [0], identity)]);
    const [guest] = guests(data);
    expect(guest.up).toBe(0); expect(guest.onboot).toBe(0); expect(guest.cpu).toBe(0);
    const page = text('guest', { ...data, entity: guest.entity });
    expect(page).toContain('Stopped'); expect(page).toContain('Disabled'); expect(page).toContain('Not measured'); expect(page).not.toContain('"tone":"bad"');
  });
  it('preserves explicit zero pool occupancy, but leaves a missing pool measurement unknown', () => {
    const result = pools(ctx([frame('pools', [0], { node: 'pve-sbx-2', storage: 'local-lvm' }), frame('pools', [null], { node: 'shared', storage: 'archive' })]));
    expect(result.map((pool) => pool.used)).toEqual([0, null]);
  });
  it('refuses to attach name-only resource samples to duplicate names', () => {
    const data = ctx([guestInfo(), frame('guestInfo', [1], { ...identity, id: 'qemu/101', node: 'pve-sbx-2' }), frame('guestCpu', [14], { name: identity.name })]);
    expect(guests(data).map((guest) => guest.cpu)).toEqual([null, null]);
    expect(text('guest', { ...data, entity: 'guest-a' })).toContain('unavailable');
  });
  it('uses full identity for duplicate names and does not cross nodes or ids', () => {
    const data = ctx([guestInfo(), frame('guestInfo', [1], { ...identity, id: 'qemu/101', node: 'pve-sbx-2' }), frame('guestCpu', [14], identity), frame('guestMemory', [80], { ...identity, node: 'pve-sbx-3' })]);
    expect(guests(data).map((guest) => guest.cpu)).toEqual([14, null]);
    expect(guests(data).map((guest) => guest.memory)).toEqual([null, null]);
  });
  it('does not select one arbitrary value from ambiguous measurements', () => {
    const data = ctx([guestInfo(), frame('guestUp', [1], identity), frame('guestUp', [0], identity)]);
    expect(guests(data)[0].up).toBeNull();
  });
  it('rejects out-of-scope platform/cluster observations and out-of-range samples', () => {
    const data = ctx([guestInfo(), frame('guestUp', [1], { ...identity, cluster: 'other' }), frame('guestCpu', [1], identity, [FROM - 1]), frame('hostInfo', [1], { instance: 'spark-1', platform: 'spark' }), frame('hostCpu', [90], { instance: 'pve-sbx-1', platform: 'spark' })]);
    expect(guests(data)[0].up).toBeNull(); expect(guests(data)[0].cpu).toBeNull();
    expect(hosts(data).map((host) => host.name)).not.toContain('spark-1'); expect(hosts(data)[0].cpu).toBeNull();
  });
  it('does not replace a null last observation with an earlier running state', () => {
    expect(guests(ctx([guestInfo(), frame('guestUp', [1, null], identity)]))[0].up).toBeNull();
  });
  it('masks host load when collection explicitly failed without conflating the API status', () => {
    const labels = { instance: 'pve-sbx-1', platform: 'pve' };
    const [host] = hosts(ctx([frame('hostUp', [0], labels), frame('hostCpu', [5], labels), frame('hostMemory', [10], labels), frame('nodeUp', [1], { id: 'node/pve-sbx-1' })]));
    expect(host.collection).toBe(0); expect(host.cpu).toBeNull(); expect(host.memory).toBeNull(); expect(host.apiUp).toBe(1);
  });
});

describe('Proxmox backup evidence boundaries', () => {
  it('never infers inclusion from absent per-guest telemetry or recorded zero totals', () => {
    expect(guests(ctx([guestInfo()]))[0].membership).toBe('unknown');
    expect(guests(ctx([guestInfo(), frame('backupInventory', [0])], { mode: 'recorded' }))[0].membership).toBe('unknown');
    const inventory = queries.find((query) => query.key === 'backupInventory');
    expect(inventory).toBeDefined();
    expect(inventory?.capture).toBeUndefined();
  });
  it('requires live capability corroboration or explicit guest evidence for membership', () => {
    expect(guests(ctx([guestInfo(), frame('backupInventory', [0])]))[0].membership).toBe('included');
    expect(guests(ctx([guestInfo(), frame('backupInventory', [1])]))[0].membership).toBe('unknown');
    expect(guests(ctx([guestInfo(), frame('guestNotBackedUp', [1], identity), frame('backupInventory', [0])]))[0].membership).toBe('excluded');
    expect(guests(ctx([guestInfo(), frame('guestNotBackedUp', [0], identity)]))[0].membership).toBe('included');
  });
  it('keeps membership separate from successful completion and recovery', () => {
    const data = ctx([guestInfo(), frame('guestNotBackedUp', [0], identity)]);
    expect(backups(data)[0].outcome).toBe('unknown');
    expect(text('backups', data)).toContain('Retained archive age, integrity and restore drills are unavailable');
  });
  it('keeps sparse successes before the displayed range when returned through the 48h lookback', () => {
    const data = ctx([completion('daily-backups', 'info', 29)]);
    const [job] = backups(data);
    expect(job.success! * 1000).toBeLessThan(FROM);
    expect(job.outcome).toBe('clean'); expect(job.dailyFreshness).toBe('within-policy');
  });
  it('retains the last error after a newer clean completion and the minimum one-hour visibility condition', () => {
    const [job] = backups(ctx([completion('daily-backups', 'error', 0.5), completion('daily-backups', 'info', 0.1)]));
    expect(job.outcome).toBe('retried'); expect(job.failure).not.toBeNull(); expect(job.failureVisibility).toBe(true);
    const [older] = backups(ctx([completion('daily-backups', 'error', 2), completion('daily-backups', 'info', 1)]));
    expect(older.outcome).toBe('retried'); expect(older.failureVisibility).toBe(false);
  });
  it('keeps a newer failure visible even when a clean completion remains within daily policy', () => {
    const [job] = backups(ctx([completion('daily-backups', 'info', 6), completion('daily-backups', 'error', 2)]));
    expect(job.outcome).toBe('failed'); expect(job.dailyFreshness).toBe('within-policy'); expect(job.failureVisibility).toBe(true);
  });
  it('does not let a manual success or another node clear a scheduled failure', () => {
    const data = ctx([completion('daily-backups', 'error', 2), completion('manual', 'info', 1), completion('daily-backups', 'info', 1, 'pve-sbx-2')]);
    const jobs = backups(data);
    expect(jobs.find((job) => job.key === 'pve-sbx-1:daily-backups')?.outcome).toBe('failed');
    expect(jobs.find((job) => job.key === 'pve-sbx-1:daily-backups')?.dailyFreshness).toBe('unknown');
    expect(jobs.find((job) => job.key === 'pve-sbx-1:manual')?.dailyFreshness).toBe('not-daily');
  });
  it('applies the repository 30h boundary, and rejects zero, future and expired completion values', () => {
    expect(backups(ctx([completion('daily-backups', 'info', 30)]))[0].dailyFreshness).toBe('outside-policy');
    for (const age of [-1, 49]) expect(backups(ctx([completion('daily-backups', 'info', age)]))[0].outcome).toBe('unknown');
    expect(backups(ctx([frame('backupCompletion', [0], { instance: 'pve-sbx-1', backup_job: 'daily-backups', outcome: 'info' })]))[0].outcome).toBe('unknown');
  });
});

describe('Proxmox recorded composition and integration', () => {
  it('loads actual guest, host and pool evidence without manufacturing missing detail', () => {
    const data = ctx(recorded, { mode: 'recorded' });
    const rows = guests(data);
    expect(rows.map((guest) => guest.name)).toEqual(['hermes-1', 'netbird-pilot-router']);
    expect(rows.every((guest) => guest.node === 'pve-sbx-1' && guest.up === 1 && guest.cpu! > 0 && guest.memory! > 60)).toBe(true);
    expect(rows.every((guest) => guest.id == null && guest.onboot == null && guest.diskSize == null && guest.membership === 'unknown')).toBe(true);
    expect(hosts(data)).toHaveLength(3); expect(hosts(data).every((host) => host.collection === 1 && host.cpu! > 0)).toBe(true);
    expect(pools(data)).toHaveLength(8); expect(pools(data).filter((pool) => pool.used === 0)).toHaveLength(2);
    expect(backups(data).every((job) => job.outcome === 'unknown')).toBe(true);
    expect(text('backups', data)).toContain('not captured');
  });
  it('builds all pages and constrains entity charts and adjacent resource tables', () => {
    const data = ctx(recorded, { mode: 'recorded' });
    for (const page of domain.pages) expect(page.sections(data).length).toBeGreaterThan(0);
    const guestSections = sections('guest', { ...data, entity: 'hermes-1' });
    expect(JSON.stringify(guestSections)).not.toContain('netbird-pilot-router');
    expect(guestSections.filter((section) => section.kind === 'chart').every((section) => section.entityLabels?.includes('name'))).toBe(true);
    const nodeSections = sections('node', { ...data, entity: 'pve-sbx-2' });
    expect(JSON.stringify(nodeSections)).not.toContain('hermes-1');
    expect(nodeSections.filter((section) => section.kind === 'chart').every((section) => section.entityLabels?.includes('instance'))).toBe(true);
    expect(nodeSections.flatMap((section) => section.kind === 'table' && section.id === 'pools' ? section.rows : []).some((row) => JSON.stringify(row).includes('Shared NFS'))).toBe(true);
  });
  it('uses the shared links to preserve range, source and entity across resource drilldowns', () => {
    const data = ctx(recorded, { mode: 'recorded', entity: 'hermes-1' });
    const collect = (list: DomainSection[]) => list.flatMap((section) => section.kind === 'table' ? section.rows.flatMap((row) => row.cells.flatMap((cell) => typeof cell === 'string' || !cell.href ? [] : [cell.href])) : section.kind === 'links' ? section.items.map((item) => item.href) : []);
    const urls = collect(sections('guest', data));
    expect(urls.some((url) => url.includes('entity=hermes-1'))).toBe(true);
    expect(urls.some((url) => url.includes('entity=pve-sbx-1'))).toBe(true);
    for (const url of urls) { const params = new URL(url, 'http://preview.test').searchParams; expect(params.get('from')).toBe(String(FROM)); expect(params.get('to')).toBe(String(TO)); expect(params.get('var-source')).toBe('replay'); }
  });
  it('keeps all refs unique, scoped, documented and backed by locators rather than embedded samples', () => {
    expect(domain.id).toBe('pve'); expect(domain.focus).toBe('Proxmox');
    expect(new Set(queries.map((query) => query.refId)).size).toBe(queries.length);
    for (const query of queries) {
      expect(query.refId).toMatch(/^D_pve_/); expect(query).not.toHaveProperty('series');
      expect(query.expr).not.toContain('vector(0)');
      if (query.expr.includes('node_') || query.key === 'hostUp' || query.key === 'hostAge') { expect(query.expr).toContain('platform="pve"'); expect(query.expr).not.toContain('cluster='); }
      else expect(query.expr).toContain('cluster="pve-sbx"');
      const detail = evidence.queries[query.key as keyof typeof evidence.queries]; expect(detail).toBeDefined();
      if (query.capture) {
        const source = query.capture.kind === 'bounded' ? bounded[query.capture.range ? 'range' : 'instant'][query.capture.key] : catalog.find((item) => item.refId === query.capture!.key)!;
        expect(source).toBeDefined(); if (source.expr !== query.expr) expect(detail).toHaveProperty('capturedExpression', source.expr);
      }
    }
    const completionQuery = queries.find((query) => query.key === 'backupCompletion')!;
    expect(completionQuery.expr).toContain('platform="pve"'); expect(completionQuery.expr).toContain('[48h]'); expect(completionQuery.capture).toBeUndefined();
    for (const page of domain.pages) for (const required of page.required) expect(queries.some((query) => query.refId === required)).toBe(true);
  });
});
