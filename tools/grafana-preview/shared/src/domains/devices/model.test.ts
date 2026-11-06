import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import type { TimeRange } from '@grafana/data';
import type { DomainContext } from '../contract';
import type { FrameLike } from '../../model/semantics';
import { domain } from './index';
import { hosts, queries, recovery, ref, timeService, timeSources } from './model';

interface Capture { expr: string; series: Array<{ labels: Record<string, string>; t: number[]; v: Array<number | null> }> }
const bounded: { from: number; to: number; instant: Record<string, Capture>; range: Record<string, Capture> } = JSON.parse(readFileSync(new URL('../../../../fixtures/bounded.json', import.meta.url), 'utf8'));
const catalog: Array<Capture & { refId: string }> = JSON.parse(readFileSync(new URL('../../../../runtime/catalog.json', import.meta.url), 'utf8'));
const host = 'ups-office';
const frame = (key: string, value: number | null, labels: Record<string, string> = { instance: host }, at = bounded.to): FrameLike => ({ refId: ref(key), fields: [
  { name: 'Time', type: 'time', values: [at] }, { name: 'Value', type: 'number', labels, values: [value] },
] });
function context(series: FrameLike[], extra: Partial<DomainContext> = {}): DomainContext {
  const link = (path: string, params: object) => `${path}?${new URLSearchParams({ from: String(bounded.from), to: String(bounded.to), source: 'recorded', ...Object.fromEntries(Object.entries(params).filter(([, value]) => value != null).map(([key, value]) => [key, String(value)])) })}`;
  return { series, mode: 'live', timeRange: { from: { valueOf: () => bounded.from }, to: { valueOf: () => bounded.to } } as unknown as TimeRange,
    timeZone: 'UTC', entity: null, coverage: { freshness: 'fresh', missing: 0, retained: 0, retainedAt: null, unanswered: false },
    href: (route) => link('/preview', route), deeper: (uid, vars) => link(`/d/${uid}`, vars ?? {}), ...extra };
}
const captured = queries.flatMap((query) => {
  const capture = query.capture;
  if (!capture) return [];
  const source = capture.kind === 'catalog' ? catalog.find((item) => item.refId === capture.key) : bounded[capture.range ? 'range' : 'instant'][capture.key];
  if (!source) throw new Error(`Missing capture ${query.refId}`);
  return source.series.map((item) => ({ refId: query.refId, fields: [
    { name: 'Time', type: 'time', values: item.t, config: { interval: query.range ? 300000 : 0 } }, { name: 'Value', type: 'number', labels: item.labels, values: item.v },
  ] }));
});
const sections = (id: string, ctx: DomainContext) => domain.pages.find((page) => page.id === id)!.sections(ctx);
const tracking = (stratum = 2, age = 10) => [frame('ntpUp', 1), frame('chronyUp', 1), frame('ntpScrapeAge', 1), frame('stratum', stratum), frame('referenceAge', age)];

describe('Supporting devices evidence', () => {
  it('retains all four named Pis when no host observations are available', () => {
    expect(hosts(context([]))).toHaveLength(4);
    expect(hosts(context([])).every((item) => item.collection === 'Unknown' && item.cpu == null && item.temperature == null)).toBe(true);
  });
  it('joins real capture identities without mixing platforms, temps or resources', () => {
    const rows = hosts(context(captured, { mode: 'recorded' }));
    expect(rows.map((row) => row.id)).toEqual(['adsb-office', 'streamer-basement-workbench', 'ups-compute-rack', 'ups-office']);
    const office = rows.find((row) => row.id === host)!;
    expect(office.collection).toBe('Scrape observed'); expect(office.temperature).toBe(69.95);
    expect(office.memory).toBeCloseTo(50.5438); expect(office.cpu).toBeCloseTo(0.1583);
  });
  it('keeps zero measured CPU but does not invent CPU for a missing or ambiguous series', () => {
    const labels = { instance: host, platform: 'pi' };
    expect(hosts(context([frame('cpu', 0, labels)])).find((row) => row.id === host)?.cpu).toBe(0);
    expect(hosts(context([frame('cpu', 0, labels), frame('cpu', 80, labels)])).find((row) => row.id === host)?.cpu).toBeNull();
    expect(hosts(context([frame('cpu', 99, { instance: host, platform: 'spark' })])).find((row) => row.id === host)?.cpu).toBeNull();
  });
  it('does not backfill an old host observation or null thermal sample', () => {
    const labels = { instance: host, platform: 'pi' };
    expect(hosts(context([frame('cpu', 50, labels, bounded.from), frame('piTemp', null)])).find((row) => row.id === host)).toMatchObject({ cpu: null, temperature: null });
  });
  it('successful GLKVM scrape is collection evidence and never a recovery test', () => {
    expect(recovery(context(captured, { mode: 'recorded' }))).toMatchObject({ collection: 'Scrape observed', temperature: 41.97 });
    const table = sections('host', context(captured, { entity: 'glkvm' })).find((section) => section.id === 'recovery-console');
    expect(JSON.stringify(table)).toContain('Not observed'); expect(JSON.stringify(table)).toContain('does not prove usable video');
  });
  it('does not trust the captured KVM fallback zero but recognizes a direct live failure', () => {
    expect(recovery(context([frame('kvmUp', 0, {})], { mode: 'recorded' })).collection).toBe('Unknown');
    expect(recovery(context([frame('kvmUp', 0, {})])).collection).toBe('Scrape failed');
    expect(queries.find((query) => query.key === 'kvmUp')?.expr).not.toContain('vector(0)');
  });
  it('does not fabricate captured chrony data, even if live-only frames leak into recorded context', () => {
    expect(timeService(context(tracking(), { mode: 'recorded' }), host).state).toBe('Unknown');
    expect(JSON.stringify(sections('time', context(captured, { mode: 'recorded' })))).toContain('were not captured');
  });
  it('requires recent successful exporter and chrony evidence before reporting synchronized tracking', () => {
    expect(timeService(context(tracking()), host).state).toBe('Tracking synchronized');
    expect(timeService(context([frame('stratum', 2), frame('referenceAge', 1)]), host).state).toBe('Unknown');
    expect(timeService(context(tracking().filter((item) => item.refId !== ref('ntpScrapeAge'))), host).state).toBe('Unknown');
    expect(timeService(context([...tracking().filter((item) => item.refId !== ref('chronyUp')), frame('chronyUp', 0)]), host).state).toBe('Partial collection');
  });
  it('follows the repository NTP rules for stale scrape, unsync and holdover', () => {
    expect(timeService(context(tracking(0)), host).state).toBe('Unsynchronized');
    expect(timeService(context(tracking(16)), host).state).toBe('Unsynchronized');
    expect(timeService(context(tracking(2, 3601)), host).state).toBe('Holdover');
    expect(timeService(context([...tracking().filter((item) => item.refId !== ref('ntpScrapeAge')), frame('ntpScrapeAge', 300)]), host).state).toBe('No recent scrape');
    expect(timeService(context([...tracking().filter((item) => item.refId !== ref('ntpUp')), frame('ntpUp', 0)]), host).state).toBe('Collection failed');
  });
  it('keeps NTP source identity and last-eight-polls semantics; inactive states are not current states', () => {
    const labels = { instance: host, source_address: '192.168.100.1' };
    const rows = timeSources(context([frame('sourceReach', 0.5, labels), frame('sourceAge', 42, labels), frame('sourceState', 1, { ...labels, source_state: 'sync' }), frame('sourceState', 0, { ...labels, source_state: 'unreach' })]));
    expect(rows[0]).toMatchObject({ instance: host, address: labels.source_address, reach: 4, age: 42, state: 'sync' });
    expect(timeSources(context([frame('sourceAge', null, labels)]))[0]).toMatchObject({ reach: null, age: null, state: 'Unknown' });
  });
  it('filters native host history by entity and does not apply host filtering to anonymous KVM recordings', () => {
    const hostCharts = sections('host', context(captured, { entity: host })).filter((section) => section.kind === 'chart');
    expect(hostCharts.every((chart) => chart.entityLabels?.includes('instance'))).toBe(true);
    const kvmCharts = sections('host', context(captured, { entity: 'glkvm' })).filter((section) => section.kind === 'chart');
    expect(kvmCharts.every((chart) => !chart.entityLabels)).toBe(true);
    expect(sections('host', context(captured, { entity: 'invalid-host' }))[0].id).toBe('host-unknown');
  });
  it('preserves source/range in links and routes the selected UPS collector to its UPS', () => {
    const page = sections('host', context(captured, { entity: host }));
    const ups = page.find((section) => section.id === 'collected-ups');
    if (ups?.kind !== 'links') throw new Error('Missing UPS relationship');
    const link = new URL(ups.items[0].href, 'http://preview.test');
    expect(link.searchParams.get('entity')).toBe('office'); expect(link.searchParams.get('focus')).toBe('Power');
    expect(link.searchParams.get('source')).toBe('recorded'); expect(link.searchParams.get('to')).toBe(String(bounded.to));
    expect(JSON.stringify(page)).toContain('server=ups-office');
  });
  it('every capture locator resolves and manifests contain unique domain refs, never samples', () => {
    expect(new Set(queries.map((query) => query.refId)).size).toBe(queries.length);
    for (const query of queries) { expect(query.refId).toBe(ref(query.key)); expect(query).not.toHaveProperty('series'); }
    expect(queries.filter((query) => query.key.startsWith('ntp') || query.expr.includes('chrony_')).every((query) => !query.capture)).toBe(true);
  });
});
