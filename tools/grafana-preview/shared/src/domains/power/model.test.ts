import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import type { TimeRange } from '@grafana/data';
import type { DomainContext } from '../contract';
import type { FrameLike } from '../../model/semantics';
import { domain } from './index';
import { inputState, queries, ref, units } from './model';

interface Capture { expr: string; series: Array<{ labels: Record<string, string>; t: number[]; v: Array<number | null> }> }
const bounded: { from: number; to: number; instant: Record<string, Capture>; range: Record<string, Capture> } = JSON.parse(readFileSync(new URL('../../../../fixtures/bounded.json', import.meta.url), 'utf8'));
const frame = (key: string, value: number | null, ups = 'compute-rack', at = bounded.to): FrameLike => ({ refId: ref(key), fields: [
  { name: 'Time', type: 'time', values: [at] }, { name: 'Value', type: 'number', labels: { ups }, values: [value] },
] });
const range = { from: { valueOf: () => bounded.from }, to: { valueOf: () => bounded.to } } as unknown as TimeRange;
function context(series: FrameLike[], extra: Partial<DomainContext> = {}): DomainContext {
  const link = (path: string, params: object) => `${path}?${new URLSearchParams({ from: String(bounded.from), to: String(bounded.to), source: 'recorded', ...Object.fromEntries(Object.entries(params).filter(([, value]) => value != null).map(([key, value]) => [key, String(value)])) })}`;
  return { series, mode: 'live', timeRange: range, timeZone: 'UTC', entity: null,
    coverage: { freshness: 'fresh', missing: 0, retained: 0, retainedAt: null, unanswered: false },
    href: (route) => link('/preview', route), deeper: (uid, vars) => link(`/d/${uid}`, vars ?? {}), ...extra };
}
const captured = queries.flatMap((query) => {
  if (!query.capture) return [];
  const source = bounded[query.capture.range ? 'range' : 'instant'][query.capture.key];
  if (!source) throw new Error(`Missing capture: ${query.refId}`);
  return source.series.map((item) => ({ refId: query.refId, fields: [
    { name: 'Time', type: 'time', values: item.t, config: { interval: query.range ? 300000 : 0 } }, { name: 'Value', type: 'number', labels: item.labels, values: item.v },
  ] }));
});
const sections = (id: string, ctx: DomainContext) => domain.pages.find((page) => page.id === id)!.sections(ctx);

describe('Power operating evidence', () => {
  it('keeps all three expected units with unknown measurements when absent', () => {
    expect(units(context([]))).toHaveLength(3);
    for (const unit of units(context([]))) {
      expect(unit.load).toBeNull(); expect(unit.runtime).toBeNull(); expect(unit.inputState).toBe('unknown');
    }
  });
  it('full charge never fills a missing runtime or establishes mains', () => {
    const unit = units(context([frame('charge', 100)]))[0];
    expect(unit.charge).toBe(100); expect(unit.runtime).toBeNull(); expect(unit.inputState).toBe('unknown');
    expect(JSON.stringify(sections('unit', context([frame('charge', 100)], { entity: 'compute-rack' })))).toContain('Unknown');
  });
  it('does not call a zero OB flag confirmation of mains', () => {
    expect(inputState(0, null)).toBe('not-asserted'); expect(inputState(0, 1)).toBe('mains');
    expect(inputState(1, 0)).toBe('battery'); expect(inputState(1, 1)).toBe('conflicting');
    expect(inputState(null, 1)).toBe('unknown'); expect(inputState(2, null)).toBe('unknown');
  });
  it('preserves directly measured zero load and zero runtime without inferring health', () => {
    const unit = units(context([frame('load', 0), frame('runtimeMin', 0), frame('onBattery', 1)]))[0];
    expect(unit.load).toBe(0); expect(unit.runtime).toBe(0); expect(unit.inputState).toBe('battery');
    expect(units(context([frame('load', 110)]))[0].load).toBe(110);
  });
  it('duplicate UPS series are ambiguous rather than first-wins', () => {
    expect(units(context([frame('runtimeMin', 9), frame('runtimeMin', 50)]))[0].runtime).toBeNull();
  });
  it('does not read another unit, another window, nulls or invalid percentages', () => {
    expect(units(context([frame('load', 20, 'office')]))[0].load).toBeNull();
    expect(units(context([frame('load', 20, 'compute-rack', bounded.from)]))[0].load).toBeNull();
    expect(units(context([frame('load', null), frame('charge', 101), frame('runtimeMin', -1)]))[0]).toMatchObject({ load: null, charge: null, runtime: null });
  });
  it('resolves the real bounded captures without mixing runtime or source identities', () => {
    const result = units(context(captured, { mode: 'recorded' }));
    expect(result.map((unit) => unit.id)).toEqual(['compute-rack', 'network-rack', 'office']);
    expect(result[0]).toMatchObject({ load: 53, charge: 100, inputVoltage: 118, inputState: 'not-asserted', collection: 'Not captured' });
    expect(result[0].runtime).toBeCloseTo(9.5833); expect(result[1].runtime).toBe(42.5); expect(result[2].load).toBe(0);
  });
  it('cannot accidentally promote live-only status frames into recorded evidence', () => {
    const unit = units(context([frame('online', 1), frame('collector', 1), frame('onBattery', 0)], { mode: 'recorded' }))[0];
    expect(unit.collection).toBe('Not captured'); expect(unit.inputState).toBe('not-asserted');
  });
  it('uses entity-filtered native charts on detail and rejects unknown entity fallback', () => {
    const charts = sections('unit', context(captured, { entity: 'office' })).filter((section) => section.kind === 'chart');
    expect(charts.length).toBeGreaterThanOrEqual(2);
    expect(charts.every((chart) => chart.entityLabels?.includes('ups'))).toBe(true);
    expect(sections('unit', context(captured, { entity: 'does-not-exist' })).map((section) => section.id)).toEqual(['unit-unknown']);
  });
  it('keeps range/source in resource links and the correct native collector identity', () => {
    const page = sections('unit', context(captured, { entity: 'network-rack' }));
    const tools = page.find((section) => section.kind === 'links');
    expect(tools?.kind).toBe('links');
    if (tools?.kind !== 'links') return;
    const native = new URL(tools.items[0].href, 'http://preview.test');
    expect(native.pathname).toBe('/d/synology-health'); expect(native.searchParams.get('node')).toBe('nas');
    expect(native.searchParams.get('source')).toBe('recorded'); expect(native.searchParams.get('from')).toBe(String(bounded.from));
    expect(JSON.stringify(sections('unit', context(captured, { entity: 'compute-rack' })))).toContain('focus=Devices');
  });
  it('query manifests resolve captures exactly, keep globally unique refs and contain no samples', () => {
    expect(new Set(queries.map((query) => query.refId)).size).toBe(queries.length);
    for (const query of queries) {
      expect(query.refId).toBe(ref(query.key)); expect(query).not.toHaveProperty('series');
      if (query.capture) expect(query.expr).toBe(bounded[query.capture.range ? 'range' : 'instant'][query.capture.key].expr);
    }
  });
});
