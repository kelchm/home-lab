import { readFileSync } from 'node:fs';
import type { TimeRange } from '@grafana/data';
import { describe, expect, it } from 'vitest';
import type { DomainContext, DomainSection } from '../contract';
import type { FrameLike } from '../../model/semantics';
import { domain } from './index';
import { atEnd, assertion, continuous, domainContext, drives, DSM_TARGET, health, incidents, PROBE, queries, raidStatus, ref, signal, volumes } from './model';

interface Captured { labels: Record<string, string>; t: number[]; v: Array<number | null>; step?: number }
interface Capture { expr: string; series: Captured[] }
interface Bounded { from: number; to: number; instant: Record<string, Capture>; range: Record<string, Capture> }
const root = new URL('../../../../', import.meta.url);
const bounded: Bounded = JSON.parse(readFileSync(new URL('fixtures/bounded.json', root), 'utf8'));
const catalog: Array<Capture & { refId: string }> = JSON.parse(readFileSync(new URL('runtime/catalog.json', root), 'utf8'));
const FROM = bounded.from;
const TO = bounded.to;
const frame = (key: string, values: Array<number | null>, labels: Record<string, string> = {}, times = values.map((_, i) => TO - (values.length - 1 - i) * 60000), interval = 60000): FrameLike => ({
  refId: ref(key), fields: [{ name: 'Time', type: 'time', values: times, config: { interval } }, { name: 'Value', type: 'number', labels, values }],
});
function context(series: FrameLike[] = [], overrides: Partial<DomainContext> = {}, from = FROM, to = TO): DomainContext {
  const timeRange = { from: { valueOf: () => from }, to: { valueOf: () => to }, raw: { from, to } } as unknown as TimeRange;
  const params = (extra: Record<string, string>) => new URLSearchParams({ from: String(from), to: String(to), 'var-source': 'replay', ...extra }).toString();
  return {
    series, mode: 'recorded', entity: null, timeZone: 'UTC', timeRange,
    coverage: { freshness: 'fresh', missing: 0, retained: 0, retainedAt: null, unanswered: false },
    href: (route) => `/preview?${params(Object.fromEntries(Object.entries({ focus: 'Athena', ...route }).filter(([, v]) => v != null).map(([k, v]) => [k, String(v)])))}`,
    deeper: (uid, vars) => `/d/${uid}?${params(Object.fromEntries(Object.entries(vars ?? {}).filter(([, v]) => v != null).map(([k, v]) => [`var-${k}`, String(v)])))}`,
    ...overrides,
  };
}
function capturedFrames(): FrameLike[] {
  return queries.flatMap((query) => {
    if (!query.capture) return [];
    const locator = query.capture;
    const capture = locator.kind === 'bounded' ? bounded[locator.range ? 'range' : 'instant'][locator.key] : catalog.find((entry) => entry.refId === locator.key);
    if (!capture) throw Error(`Missing capture ${query.refId}`);
    return capture.series.map((series) => frame(query.key, series.v, series.labels, series.t, series.step ?? (locator.range ? 300000 : 60000)));
  });
}
const captured = capturedFrames();
const page = (id: string, ctx = context(captured)) => domain.pages.find((item) => item.id === id)!.sections(ctx);
const tables = (sections: DomainSection[]) => sections.filter((section) => section.kind === 'table');
const links = (sections: DomainSection[]) => sections.flatMap((section) => section.kind === 'links' ? section.items : section.kind === 'text' ? section.links ?? [] : section.kind === 'table' ? section.rows.flatMap((row) => row.cells.filter((cell) => typeof cell !== 'string' && cell.href).map((cell) => ({ href: typeof cell === 'string' ? cell : cell.href! }))) : []);

describe('Athena capture and query contract', () => {
  it('exports Athena with bounded domain/workload/detail destinations', () => {
    expect(domain.id).toBe('nas'); expect(domain.focus).toBe('Athena');
    expect(domain.pages.map((item) => item.id)).toEqual(['overview', 'storage', 'drives', 'incident', 'drive']);
    for (const item of domain.pages) expect(item.sections(context())).not.toHaveLength(0);
  });
  it('uses unique refs and exact capture expressions without embedding samples', () => {
    expect(new Set(queries.map((q) => q.refId)).size).toBe(queries.length);
    for (const query of queries) {
      expect(query.refId).toBe(ref(query.key)); expect(query.scope).toBe('nas');
      expect(query).not.toHaveProperty('series');
      if (!query.capture) { expect(query.optional).toBe(true); continue; }
      const cap = query.capture;
      const original = cap.kind === 'bounded' ? bounded[cap.range ? 'range' : 'instant'][cap.key] : catalog.find((q) => q.refId === cap.key);
      expect(original?.expr).toBe(query.expr);
    }
    expect(queries.some((q) => /or vector\(0\)/.test(q.expr))).toBe(false);
  });
  it('declares valid required refs and native charts for actual activity', () => {
    const refs = new Set(queries.map((q) => q.refId));
    for (const item of domain.pages) {
      for (const required of item.required) expect(refs.has(required)).toBe(true);
      for (const section of item.sections(context(captured, { entity: item.id === 'drive' ? 'Disk 1' : PROBE }))) {
        if (section.kind === 'chart') for (const chartRef of section.refs) expect(refs.has(chartRef)).toBe(true);
      }
    }
    expect(page('overview').some((section) => section.kind === 'chart' && section.refs.includes(ref('written')))).toBe(true);
  });
});

describe('DSM incident semantics', () => {
  it('reconstructs the recorded 5h27 assertion failure without inferring a NAS outage', () => {
    const events = incidents(context(captured));
    expect(events).toHaveLength(1);
    expect(events[0]).toMatchObject({ start: 1793539560000, end: 1793559180000, duration: (5 * 60 + 27) * 60000, resolved: true, clipped: false, http404: true, collectorContinuity: true });
    expect(signal(context(captured), 'assertion', { probe: PROBE })).toBe(1);
    expect(signal(context(captured), 'httpCode', { probe: PROBE })).toBe(200);
    const text = JSON.stringify(page('incident', context(captured, { entity: PROBE })));
    expect(text).toContain('HTTP 404 in sampled failure window');
    expect(text).toContain('do not establish the cause');
  });
  it('does not substitute HTTP 200 or collector success for assertion success', () => {
    const ctx = context([frame('httpCode', [200], { probe: PROBE }), frame('scrapes', [1])]);
    expect(assertion(signal(ctx, 'assertion', { probe: PROBE })).tone).toBe('unknown');
    const row = tables(page('overview', ctx)).find((t) => t.id === 'service-and-collection')!.rows[0];
    expect(row.cells[1]).toEqual({ text: 'Unknown', tone: 'unknown' });
  });
  it('splits failures at gaps and never marks the first span recovered', () => {
    const from = TO - 600000;
    const data = [frame('assertionHistory', [1, 0, 0, 0, 1], { probe: PROBE }, [from, from + 60000, from + 120000, from + 480000, from + 540000])];
    const events = incidents(context(data, {}, from, TO));
    expect(events).toHaveLength(2);
    expect(events[0].resolved).toBe(false); expect(events[1].resolved).toBe(true);
    expect(events[0].collectorContinuity).toBe(false);
  });
  it('requires a passing sample to establish recovery, including explicit missing values', () => {
    const events = incidents(context([frame('assertionHistory', [1, 0, null], { probe: PROBE })]));
    expect(events).toHaveLength(1); expect(events[0].resolved).toBe(false);
  });
  it('labels clipped failure starts and unknown recovery at the range end', () => {
    const from = TO - 120000;
    const events = incidents(context([frame('assertionHistory', [0, 0, 0], { probe: PROBE })], {}, from, TO));
    expect(events[0]).toMatchObject({ clipped: true, resolved: false, duration: 120000 });
  });
  it('does not call a known transition one cadence after the range start clipped', () => {
    const events = incidents(context([frame('assertionHistory', [1, 0, 1], { probe: PROBE })], {}, TO - 120000, TO));
    expect(events[0]).toMatchObject({ clipped: false, startUncertain: false, resolved: true, duration: 60000 });
  });
  it('does not claim collector continuity across missing or failed scrapes', () => {
    expect(continuous({ labels: {}, step: 60000, points: [[0, 1], [60000, 0], [120000, 1]] }, 0, 120000, (n) => n === 1)).toBe(false);
    expect(continuous({ labels: {}, step: 60000, points: [[0, 1], [120000, 1]] }, 0, 120000, (n) => n === 1)).toBe(false);
    expect(continuous(undefined, 0, 120000, (n) => n === 1)).toBe(false);
  });
  it('keeps an ambiguous duplicate probe history unknown', () => {
    expect(incidents(context([frame('assertionHistory', [1, 0, 1], { probe: PROBE }), frame('assertionHistory', [1, 1, 1], { probe: PROBE })]))).toEqual([]);
  });
});

describe('Storage and per-bay evidence', () => {
  it('joins eight captured bays by identity, retaining real zero counters', () => {
    const rows = drives(context(captured)); expect(rows).toHaveLength(8);
    expect(rows[0]).toMatchObject({ id: 'Disk 1', model: 'ST14000NM001G-2KJ103', temperature: 36, reallocated: 0, pending: 0, uncorrectable: 0, capacity: null });
    expect(rows.every((drive) => drive.health.text === 'Reported healthy')).toBe(true);
    expect(volumes(context(captured))[0]).toMatchObject({ id: 'Volume 1', usedPercent: 32.86969245587245, total: null, free: null });
  });
  it('does not borrow health, temperature or model from another bay', () => {
    const rows = drives(context([frame('diskInfo', [1], { disk: 'Disk 1', model_name: 'one' }), frame('diskHealth', [0], { disk: 'Disk 2' })]));
    expect(rows[0]).toMatchObject({ id: 'Disk 1', health: { tone: 'unknown' }, temperature: null, pending: null });
    expect(rows[1]).toMatchObject({ id: 'Disk 2', model: null, health: { tone: 'bad' } });
  });
  it('rejects ambiguous duplicate series rather than selecting the first', () => {
    const ctx = context([frame('diskHealth', [1], { disk: 'Disk 1' }), frame('diskHealth', [0], { disk: 'Disk 1' })]);
    expect(signal(ctx, 'diskHealth', { disk: 'Disk 1' })).toBeNull();
    expect(drives(ctx)[0].health.tone).toBe('unknown');
  });
  it('preserves failure, warning, initialization and unknown codes independently', () => {
    const rows = drives(context([frame('diskHealth', [2], { disk: 'Disk 1' }), frame('diskStatus', [3], { disk: 'Disk 1' }), frame('diskStatus', [5], { disk: 'Disk 2' })]));
    expect(rows[0].health.tone).toBe('warning'); expect(rows[0].status.text).toBe('Not initialized'); expect(rows[1].status.tone).toBe('bad');
    expect(health(99).tone).toBe('unknown'); expect(raidStatus(11).text).toBe('Degraded'); expect(raidStatus(null).tone).toBe('unknown');
  });
  it('does not carry old endpoint values into a different window', () => {
    expect(signal(context([frame('system', [1], {}, [FROM])]), 'system')).toBeNull();
    expect(atEnd({ labels: {}, step: 60000, points: [[TO + 1, 1]] }, context())).toBeNull();
    expect(signal(context([frame('system', [1, null])]), 'system')).toBeNull();
  });
  it('keeps uncaptured optional data unavailable in recorded mode', () => {
    const ctx = context([frame('volumeTotal', [1024 ** 4], { raidName: 'Volume 1' }), frame('driveBusy', [0], { disk: 'Disk 1' })]);
    expect(domainContext(ctx).series).toEqual([]);
    expect(domainContext({ ...ctx, mode: 'live' }).series).toHaveLength(2);
    expect(JSON.stringify(page('storage', ctx))).not.toContain('1 TiB');
  });
  it('does not manufacture historical charts from window-end values or global totals', () => {
    const details = page('drive', context(captured, { entity: 'Disk 1' }));
    const charts = details.filter((section) => section.kind === 'chart');
    expect(charts).toHaveLength(4);
    expect(charts.every((section) => section.entityLabels?.includes('disk'))).toBe(true);
    expect(charts.flatMap((section) => section.refs)).not.toContain(ref('read'));
    expect(charts.flatMap((section) => section.refs)).not.toContain(ref('diskTemp'));
  });
  it('does not render another bay for an unobserved selected entity', () => {
    const details = page('drive', context(captured, { entity: 'Disk 99' }));
    expect(details).toHaveLength(1); expect(details[0].id).toBe('drive-unavailable');
  });
  it('rejects impossible volume capacity values instead of showing available space', () => {
    const rows = volumes(context([frame('volumes', [-5], { raidName: 'Volume 1' }), frame('volumeTotal', [100], { raidName: 'Volume 1' }), frame('volumeFree', [200], { raidName: 'Volume 1' })]));
    expect(rows[0]).toMatchObject({ usedPercent: null, free: null });
  });
});

describe('Athena investigation journeys', () => {
  it('carries range and source through incident, bay and native probe links', () => {
    const incidentHref = links(page('overview')).find((link) => link.href.includes('view=incident'))!.href;
    expect(incidentHref).toContain(`entity=${PROBE}`);
    const driveHref = links(page('drives')).find((link) => link.href.includes('view=drive&'))!.href;
    expect(driveHref).toContain('entity=Disk+1');
    const native = links(page('incident', context(captured, { entity: PROBE }))).find((link) => link.href.includes('/d/pi-blackbox'))!;
    expect(new URL(native.href, 'http://preview').searchParams.get('var-target')).toBe(DSM_TARGET);
    for (const href of [incidentHref, driveHref, native.href]) {
      const params = new URL(href, 'http://preview').searchParams;
      expect(params.get('from')).toBe(String(FROM)); expect(params.get('to')).toBe(String(TO)); expect(params.get('var-source')).toBe('replay');
    }
  });
  it('always scopes the incident assertion history to DSM, including direct and tab entry', () => {
    for (const entity of [null, PROBE]) {
      const selected = page('incident', context(captured, { entity })).find((section) => section.id === 'dsm-assertion');
      expect(selected).toMatchObject({ kind: 'chart', entityLabels: ['probe'], entity: PROBE });
    }
    expect(page('incident', context(captured, { entity: 'other-probe' }))[0].id).toBe('incident-unavailable');
  });
  it('states the NAS backup evidence gap on both overview and storage', () => {
    for (const id of ['overview', 'storage']) {
      const text = JSON.stringify(page(id));
      expect(text).toContain('cannot establish Athena backup health');
      expect(text).not.toContain('backup healthy');
    }
  });
});
