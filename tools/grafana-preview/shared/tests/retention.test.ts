import { describe, expect, test } from 'vitest';
import type { PanelData } from '@grafana/data';
import { coverage, rangeKey, resolve, scopeKey, type ResolveContext, type RetainedByRef } from '../src/lifecycle';
import { NEEDS } from '../src/model/catalog';
import { emptyFrame, frame, range, sharedRequest } from './fixtures';

const WINDOW = range('now-24h', 'now');
const context = (over: Partial<ResolveContext> = {}): ResolveContext => ({ expected: ['A', 'B'], source: 'recorded', range: rangeKey(WINDOW), now: 1000, ...over });
const request = (exprs: Record<string, string>, uid = 'comparison', extra = {}) => ({ targets: Object.entries(exprs).map(([refId, expr]) => ({ refId, expr, datasource: { uid } })), ...extra });
const REQUEST = request({ A: 'a', B: 'b' });
const data = (state: string, series: PanelData['series'], over: Partial<PanelData> | object = {}) => ({ state, series, request: REQUEST, timeRange: WINDOW, ...over }) as unknown as PanelData;
const none: RetainedByRef = new Map();
const good = (at = 1000) => resolve(data('Done', [frame('A', [1, 2]), frame('B', [3])]), none, context({ now: at })).next;
const failing = (refs: string[], over = {}) => data('Error', [emptyFrame('A'), emptyFrame('B')], { errors: refs.map((refId) => ({ refId, message: 'boom' })), ...over });
const shown = (r: ReturnType<typeof resolve>, ref: string) => r.series.filter((f) => f.refId === ref).map((f) => f.fields[1].values[0]);

describe('what a run says about each query', () => {
  test('a failed first load has nothing to show and says so', () => {
    const r = resolve(failing(['A', 'B']), none, context());
    expect(r.series).toEqual([]);
    expect(coverage(r, ['A', 'B']).freshness).toBe('failed');
  });

  test('a successful query with no rows is empty, not failed', () => {
    const r = resolve(data('Done', [emptyFrame('A'), emptyFrame('B')]), none, context());
    expect(r.refs.get('A')?.state).toBe('empty');
    expect(coverage(r, ['A', 'B']).freshness).toBe('empty');
  });

  test('the three queries expected to be empty are never gaps, even when a path drops their frame', () => {
    const refs = ['H_P', 'H_WKN', 'H_WKX', 'H_BKV'];
    const r = resolve(data('Done', [frame('H_P', [1])]), none, context({ expected: refs }));
    expect(['H_WKN', 'H_WKX', 'H_BKV'].map((ref) => r.refs.get(ref)?.state)).toEqual(['empty', 'empty', 'empty']);
    expect(coverage(r, refs).freshness).toBe('fresh');
  });

  test('without a retained copy a partial failure stays visibly partial', () => {
    const r = resolve(data('Error', [frame('A', [3]), emptyFrame('B')], { errors: [{ refId: 'B', message: 'boom' }] }), none, context());
    const c = coverage(r, ['A', 'B']);
    expect([c.freshness, c.missing]).toEqual(['partial', 1]);
    expect(r.refs.get('A')?.state).toBe('current');
  });

  test('a request error without a refId fails every query, including via the deprecated error field', () => {
    const r = resolve(data('Error', [], { errors: undefined, error: { message: 'bad gateway', status: 502 } }), good(), context({ now: 2000 }));
    expect(coverage(r, ['A', 'B']).freshness).toBe('stale');
    expect(r.errors).toEqual(['bad gateway']);
  });
});

describe('how far an error reaches', () => {
  const expected = ['H_P', 'H_WKN'];
  const series = [frame('H_P', [1]), emptyFrame('H_WKN')];
  const withError = (refId?: string) => data('Error', series, { errors: [{ refId, message: 'boom' }], request: undefined });

  test('an error naming a catalog query this surface does not read touches nothing here', () => {
    expect(resolve(withError('R_k8s_cores'), none, context({ expected })).refs.get('H_WKN')?.state).toBe('empty');
  });

  test('an error naming anything else could be any query, so a quiet answer stops counting', () => {
    expect(resolve(withError('Source1'), none, context({ expected })).refs.get('H_WKN')?.state).toBe('failed');
    expect(resolve(withError(undefined), none, context({ expected })).refs.get('H_WKN')?.state).toBe('failed');
  });

  test('rows accompanying a failed source do not establish a successful first load', () => {
    expect(resolve(withError('Source1'), none, context({ expected })).refs.get('H_P')?.state).toBe('failed');
  });
});

describe('no causal claim without evidence', () => {
  test('a settled run with no frames and no error is unavailable, not failed', () => {
    const r = resolve(data('Done', []), none, context());
    const c = coverage(r, ['A', 'B']);
    expect(c.freshness).toBe('unavailable');
    expect(c.unanswered).toBe(true);
    expect(r.errors).toEqual([]);
  });

  test('its retained copy still stands in, marked as unanswered rather than failed', () => {
    const r = resolve(data('Done', []), good(), context({ now: 2000 }));
    expect(r.refs.get('A')).toEqual({ state: 'retained', at: 1000, because: 'unanswered' });
    expect(coverage(r, ['A', 'B']).unanswered).toBe(true);
  });
});

describe('retention is keyed by query, datasource, scope and range', () => {
  test('a failed refresh keeps the last answer and dates it', () => {
    const r = resolve(failing(['A', 'B']), good(1000), context({ now: 2000 }));
    expect(shown(r, 'A')).toEqual([1]);
    expect(coverage(r, ['A', 'B'])).toMatchObject({ freshness: 'stale', retained: 2, retainedAt: 1000 });
  });

  test('copies survive the scene clearing superseded values in place', () => {
    const source = frame('A', [1, 2]);
    const { next } = resolve(data('Done', [source, frame('B', [3])]), none, context());
    (source.fields[1].values as number[]).length = 0;
    expect(resolve(failing(['A']), next, context({ now: 2000 })).series[0].fields[1].values).toEqual([1, 2]);
  });

  test('one failed query stands in only for itself', () => {
    const r = resolve(data('Error', [frame('A', [9]), emptyFrame('B')], { errors: [{ refId: 'B', message: 'boom' }] }), good(), context({ now: 2000 }));
    expect([shown(r, 'A'), shown(r, 'B')]).toEqual([[9], [3]]);
  });

  test('a changed query under the same refId does not inherit the old answer', () => {
    const r = resolve(failing(['A', 'B'], { request: request({ A: 'edited', B: 'b' }) }), good(), context({ now: 2000 }));
    expect(shown(r, 'A')).toEqual([]);
    expect(r.refs.get('A')?.state).toBe('failed');
    expect(shown(r, 'B')).toEqual([3]);
  });

  test('a consumer that sees only its source panel still notices an edited query', () => {
    const queries = (a: string) => new Map([['A', a], ['B', 'b']]);
    const { next } = resolve(data('Done', [frame('A', [1]), frame('B', [2])], { request: sharedRequest }), none, context({ queries: queries('a') }));
    const fail = failing(['A', 'B'], { request: sharedRequest });
    expect(resolve(fail, next, context({ queries: queries('a') })).refs.get('A')?.state).toBe('retained');
    expect(resolve(fail, next, context({ queries: queries('edited') })).refs.get('A')?.state).toBe('failed');
  });

  test('the host-known source and the request datasource both invalidate', () => {
    expect(resolve(failing(['A']), good(), context({ source: 'live' })).refs.get('A')?.state).toBe('failed');
    expect(resolve(failing(['A'], { request: request({ A: 'a', B: 'b' }, 'comparison-live') }), good(), context()).refs.get('A')?.state).toBe('failed');
  });

  test('scopes, ad hoc filters and scoped variables invalidate; Grafana\'s own variables do not', () => {
    const scoped = (extra: object) => failing(['A'], { request: request({ A: 'a', B: 'b' }, 'comparison', extra) });
    expect(resolve(scoped({ scopes: [{ metadata: { name: 'lab' } }] }), good(), context()).refs.get('A')?.state).toBe('failed');
    expect(resolve(scoped({ filters: [{ key: 'k', operator: '=', value: 'v' }] }), good(), context()).refs.get('A')?.state).toBe('failed');
    expect(resolve(scoped({ scopedVars: { host: { value: 'spark-1' } } }), good(), context()).refs.get('A')?.state).toBe('failed');
    expect(resolve(scoped({ scopedVars: { __interval: { value: '5m' }, __sceneObject: { value: {} } } }), good(), context()).refs.get('A')?.state).toBe('retained');
    expect(scopeKey(undefined)).toBe(scopeKey({} as PanelData['request']));
  });

  test('refreshing a relative range keeps identity; moving the range does not', () => {
    const r = (from: string | number, values: [number, number]) => rangeKey(range(from, 'now', values));
    expect(r('now-24h', [1, 2])).toBe(r('now-24h', [3, 4]));
    expect(r('now-24h', [1, 2])).not.toBe(r('now-6h', [1, 2]));
    const moved = range('now-6h', 'now');
    expect(resolve(failing(['A'], { timeRange: moved }), good(), context({ range: rangeKey(moved) })).refs.get('A')?.state).toBe('failed');
  });

  test('a successful empty answer drops the retained copy', () => {
    const after = resolve(data('Done', [frame('A', [1]), emptyFrame('B')]), good(), context()).next;
    expect(after.has('B')).toBe(false);
    expect(resolve(failing(['B']), after, context()).refs.get('B')?.state).toBe('failed');
  });

  test('recovery clears every trace of the failure', () => {
    const stale = resolve(failing(['A', 'B']), good(1000), context({ now: 2000 }));
    const back = resolve(data('Done', [frame('A', [7]), frame('B', [8])]), stale.next, context({ now: 3000 }));
    expect(coverage(back, ['A', 'B'])).toMatchObject({ freshness: 'fresh', retained: 0, retainedAt: null, missing: 0 });
    expect(shown(back, 'A')).toEqual([7]);
  });
});

describe('while a run is in flight', () => {
  test('an ordinary refresh keeps dated retained frames while pending', () => {
    const r = resolve(data('Loading', [frame('A', [1]), frame('B', [2])]), good(), context());
    expect(coverage(r, ['A', 'B'])).toMatchObject({ freshness: 'loading', retained: 2, retainedAt: 1000 });
  });

  test('frames from another range or source are not shown under the new identity', () => {
    const r = resolve(data('Loading', [frame('A', [1]), frame('B', [2])]), good(), context({ source: 'live' }));
    expect(r.series).toEqual([]);
    expect(coverage(r, ['A', 'B']).freshness).toBe('loading');
  });

  test('a run still loading never rewrites what is retained', () => {
    const before = good();
    expect(resolve(data('Loading', [frame('A', [5])]), before, context()).next).toEqual(before);
  });

  test('values cleared in place mid-run fall back to the copy without reporting a failure', () => {
    const original = [frame('A', [1]), frame('B', [2])];
    const kept = resolve(data('Done', original), none, context()).next;
    for (const item of original) for (const field of item.fields) field.values.length = 0;
    const r = resolve(data('Loading', original), kept, context());
    expect(shown(r, 'A')).toEqual([1]);
    expect(coverage(r, ['A', 'B']).freshness).toBe('loading');
  });
});

describe('each surface answers for its own queries', () => {
  const series = NEEDS.machines.filter((ref) => ref !== 'H_P').map((ref) => frame(ref, [1]));
  const r = resolve(data('Error', [...series, emptyFrame('H_P')], { errors: [{ refId: 'H_P', message: 'boom' }], request: undefined }), none, context({ expected: [...NEEDS.machines, 'H_P', 'H_PH'] }));

  test('a failed check query leaves Machines fresh and marks Service checks', () => {
    expect(coverage(r, NEEDS.machines).freshness).toBe('fresh');
    expect(coverage(r, ['H_P', 'H_PH']).freshness).not.toBe('fresh');
  });
});
