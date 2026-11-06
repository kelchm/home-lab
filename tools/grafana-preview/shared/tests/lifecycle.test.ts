import { describe, expect, it } from 'vitest';
import type { DataFrame, PanelData, TimeRange } from '@grafana/data';
import { coverage, rangeKey, resolve, type ResolveContext } from '../src/lifecycle';

const timeRange = (from = '2026-11-01T01:27:04.636Z', to = '2026-11-02T01:27:04.636Z') => ({ raw: { from, to } } as TimeRange);
const range = timeRange();
const frame = (refId = 'A', value = 0): DataFrame => ({ refId, length: 1, fields: [{ name: 'Time', type: 'time', config: {}, values: [1793582824636] }, { name: 'Value', type: 'number', config: {}, values: [value] }] } as DataFrame);
const context = (extra: Partial<ResolveContext> = {}): ResolveContext => ({ expected: ['A'], source: 'recorded', range: rangeKey(range), now: 1000, ...extra });
const data = (extra: Partial<PanelData> = {}): PanelData => ({ state: 'Done', series: [frame()], timeRange: range, request: { targets: [{ refId: 'A', expr: 'up', datasource: { uid: 'comparison' } }], range } as unknown as PanelData['request'], ...extra } as PanelData);
const failed = (extra: Partial<PanelData> = {}) => data({ state: 'Error' as PanelData['state'], series: [], errors: [{ refId: 'A', message: 'Injected failure' }], ...extra });

describe('query identity and honest state', () => {
 it('preserves a measured zero', () => {
  const r = resolve(data(), new Map(), context());
  expect(r.refs.get('A')?.state).toBe('current');expect(r.series[0].fields[1].values[0]).toBe(0);
 });
 it('copies retained values before Grafana clears its arrays', () => {
  const d = data(); const r = resolve(d, new Map(), context());d.series[0].fields[1].values.length = 0;
  const next = resolve(failed(), r.next, context());expect(next.series[0].fields[1].values[0]).toBe(0);
 });
 it('marks a failed refresh stale and keeps its original receipt time', () => {
  const r = resolve(data(), new Map(), context());const next = resolve(failed(), r.next, context({ now: 3000 }));
  expect(coverage(next, ['A'])).toMatchObject({ freshness: 'stale', retainedAt: 1000 });
 });
 it('drops retained values when the same ref names a different query', () => {
  const r = resolve(data(), new Map(), context());const d = failed();(d.request!.targets[0] as { refId: string; expr: string }).expr = 'different_metric';
  expect(resolve(d, r.next, context()).series).toHaveLength(0);
 });
 it('drops retained values when the datasource changes', () => {
  const r = resolve(data(), new Map(), context());expect(resolve(failed(), r.next, context({ source: 'live' })).series).toHaveLength(0);
 });
 it('drops retained values when a scoped host changes', () => {
  const d = data();d.request!.scopedVars = { host: { text: 'spark-1', value: 'spark-1' } };
  const r = resolve(d, new Map(), context());const f = failed();f.request!.scopedVars = { host: { text: 'spark-2', value: 'spark-2' } };
  expect(resolve(f, r.next, context()).series).toHaveLength(0);
 });
 it('does not relabel the old settled request as data for a newly selected range', () => {
  const r = resolve(data(), new Map(), context());const shorter = timeRange('2026-11-01T19:27:04.636Z');
  const next = resolve(data(), r.next, context({ range: rangeKey(shorter) }));expect(next.series).toHaveLength(0);
 });
 it('rejects old shared frames even when the consumer has relabelled its request range', () => {
  const origins = new WeakMap<object, string>(); const initial = data();
  const r = resolve(initial, new Map(), context({ origins }));
  const shorter = timeRange('2026-11-01T19:27:04.636Z');
  const nextContext = context({ origins, range: rangeKey(shorter) });
  const relabelled = data({ series: initial.series, timeRange: shorter, request: { ...initial.request!, range: shorter } });
  const moved = resolve(relabelled, r.next, nextContext);expect(moved.series).toHaveLength(0);
  const error = resolve(failed({ timeRange: shorter, request: { ...initial.request!, range: shorter } }), moved.next, nextContext);
  expect(coverage(error, ['A']).freshness).toBe('failed');expect(error.series).toHaveLength(0);
  const fresh = resolve(data({ timeRange: shorter, request: { ...initial.request!, range: shorter } }), error.next, nextContext);
  expect(coverage(fresh, ['A']).freshness).toBe('fresh');
  expect(coverage(resolve(initial, fresh.next, context({ origins })), ['A']).freshness).toBe('fresh');
 });
 it('retains a relative window on refresh, with a stable raw range identity', () => {
  const relative = timeRange('now-24h', 'now');const c = context({ range: rangeKey(relative) });
  const r = resolve(data({ timeRange: relative, request: { ...data().request!, range: relative } }), new Map(), c);
  const next = resolve(failed({ timeRange: relative, request: { ...data().request!, range: relative } }), r.next, c);
  expect(coverage(next, ['A']).freshness).toBe('stale');
 });
 it('clears a previous value on a successful empty result', () => {
  const r = resolve(data(), new Map(), context());const f = frame();f.length = 0;f.fields.forEach(x => x.values.length = 0);
  const next = resolve(data({ series: [f] }), r.next, context());expect(next.refs.get('A')?.state).toBe('empty');expect(next.series).toHaveLength(0);
 });
 it('does not invent a connection error when a settled run returns nothing', () => {
  const r = resolve(data({ series: [] }), new Map(), context());expect(coverage(r, ['A']).freshness).toBe('unavailable');expect(r.errors).toEqual([]);
 });
 it('keeps an answered query current when a different query fails', () => {
  const r = resolve(data({ series: [frame('A', 2), frame('B', 3)] }), new Map(), context({ expected: ['A', 'B'] }));
  const next = resolve(failed({ series: [frame('B', 4)] }), r.next, context({ expected: ['A', 'B'] }));
  expect(next.refs.get('A')?.state).toBe('retained');expect(next.refs.get('B')?.state).toBe('current');
 });
 it('keeps a Dashboard source error from reading as successful empty data', () => {
  const f = frame(); f.length = 0; f.fields.forEach(x => x.values.length = 0);
  const r = resolve(failed({ series: [f], errors: [{ refId: 'Source1', message: 'Source failed' }] }), new Map(), context());
  expect(coverage(r, ['A']).freshness).toBe('failed');
 });
 it('does not mistake an empty shared-source frame for a successful empty query', () => {
  const f = frame(); f.length = 0; f.fields.forEach(x => x.values.length = 0);
  const request = { ...data().request!, targets: [{ refId: 'Shared', panelId: 1, datasource: { uid: '-- Dashboard --' } }] };
  const r = resolve(data({ series: [f], request }), new Map(), context());
  expect(coverage(r, ['A']).freshness).toBe('unavailable');
 });
 it('normalizes equivalent absolute range representations', () => {
  expect(rangeKey(timeRange(String(Date.parse(range.raw.from as string)), String(Date.parse(range.raw.to as string))))).toBe(rangeKey(range));
  expect(rangeKey(range)).toBe(rangeKey({ raw: { from: { valueOf: () => Date.parse(range.raw.from as string) }, to: { valueOf: () => Date.parse(range.raw.to as string) } } } as unknown as TimeRange));
 });
});
