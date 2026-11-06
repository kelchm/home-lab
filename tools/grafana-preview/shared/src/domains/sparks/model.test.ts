import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import type { TimeRange } from '@grafana/data';
import type { DomainContext, DomainSection } from '../contract';
import type { FrameLike } from '../../model/semantics';
import { domain } from './index';
import { aggregate, atEnd, availableFrames, deploymentKey, deployments, eventSpans, hardware, historicalWindow, HISTORY, poolFraction, queries, ref } from './model';
import evidence from './evidence.json';

const id = { instance: 'spark-1', model: 'GLM-5.3-Flash', recipe: 'recipe-a', runtime: 'tensorfold', platform: 'spark', job: 'spark-inference' };
const frame = (key: string, values: Array<number | null>, labels: Record<string, string> = {}, times = values.map((_, i) => TO - (values.length - i - 1) * 60000), interval = 60000): FrameLike => ({
  refId: ref(key), fields: [{ name: 'Time', type: 'time', values: times, config: { interval } }, { name: 'Value', type: 'number', labels, values }],
});
function ctx(series: FrameLike[], options: Partial<DomainContext> = {}, window = { from: FROM, to: TO }): DomainContext {
  const timeRange = { from: { valueOf: () => window.from }, to: { valueOf: () => window.to }, raw: window } as unknown as TimeRange;
  const route = (path: string, extra: Record<string, string>) => {
    const params = new URLSearchParams({ from: String(window.from), to: String(window.to), 'var-source': 'replay', ...extra });
    return `http://preview.test/${path}?${params}`;
  };
  return {
    series, mode: 'live', timeRange, entity: null, timeZone: 'UTC',
    coverage: { freshness: 'fresh', missing: 0, retained: 0, retainedAt: null, unanswered: false },
    href: (target) => route('preview', Object.fromEntries(Object.entries({ focus: 'Sparks', ...target }).filter(([, v]) => v != null).map(([k, v]) => [k, String(v)]))),
    deeper: (uid, variables) => route(`d/${uid}`, Object.fromEntries(Object.entries(variables ?? {}).map(([k, v]) => [`var-${k}`, v]))),
    ...options,
  };
}
const page = (name: string, context: DomainContext) => domain.pages.find((item) => item.id === name)!.sections(context);
const metricValues = (sections: DomainSection[]) => sections.flatMap((section) => section.kind === 'metrics' ? section.items.map((item) => item.value) : []);
const textOf = (sections: DomainSection[]) => JSON.stringify(sections);

interface Captured { labels: Record<string, string>; t: number[]; v: Array<number | null> }
interface Bounded { from: number; to: number; instant: Record<string, { expr: string; series: Captured[] }>; range: Record<string, { expr: string; series: Captured[] }> }
interface ApiFrame { schema: { fields: Array<{ name: string; type: string; labels?: Record<string, string>; config?: object }>; meta?: { executedQueryString?: string; custom?: { resultType?: string } } }; data: { values: Array<Array<number | null>> } }
const root = new URL('../../../../', import.meta.url);
const bounded: Bounded = JSON.parse(readFileSync(new URL('fixtures/bounded.json', root), 'utf8'));
const FROM = bounded.from;
const TO = bounded.to;
const inference: Array<{ results: Record<string, { frames: ApiFrame[] }> }> = JSON.parse(readFileSync(new URL('fixtures/inference.json', root), 'utf8'));
const catalog: Array<{ refId: string; series: Captured[] }> = JSON.parse(readFileSync(new URL('runtime/catalog.json', root), 'utf8'));
const executed = (frame: ApiFrame) => frame.schema.meta?.executedQueryString?.replace(/^Expr: /, '').split('\nStep:')[0];
const hash = (expr: string) => `expr:${createHash('sha256').update(expr).digest('hex').slice(0, 16)}`;
function capturedFrames(): FrameLike[] {
  return queries.flatMap((query) => {
    const capture = query.capture;
    if (!capture) return [];
    if (capture.kind === 'inference') {
      for (const response of inference) for (const result of Object.values(response.results)) {
        const original = result.frames.map(executed).find(Boolean);
        const ranged = result.frames[0]?.schema.meta?.custom?.resultType === 'matrix';
        if (original && hash(original) === capture.key && ranged === Boolean(capture.range)) {
          // Result-level query metadata belongs to every frame, not only the first anonymous slot.
          return result.frames.map((source) => ({ refId: query.refId, fields: source.schema.fields.map((field, i) => ({ ...field, values: source.data.values[i] })) }));
        }
      }
      throw new Error(`Unresolved inference capture ${query.refId}`);
    }
    const source = capture.kind === 'bounded' ? bounded[capture.range ? 'range' : 'instant'][capture.key] : catalog.find((entry) => entry.refId === capture.key);
    if (!source) throw new Error(`Unresolved capture ${query.refId}`);
    return source.series.map((series) => frame(query.key, series.v, series.labels, series.t));
  });
}
const captured = capturedFrames();

describe('Sparks semantic failure scenarios', () => {
  it('fresh idle keeps an occupied supported pool without inventing latency or readiness', () => {
    const data = ctx([
      frame('identity', [1], id), frame('running', [0], id), frame('waiting', [0], id),
      frame('poolTokens', [1000], id), frame('poolFree', [250], id), frame('capPool', [1], id),
      frame('aggregateOutput', [0], id), frame('capOutput', [1], id), frame('capLatency', [1], id), frame('capDecode', [1], id),
    ]);
    const [deployment] = deployments(data);
    expect(deployment.running.value).toBe(0);
    expect(deployment.waiting.value).toBe(0);
    expect(deployment.aggregateOutput.value).toBe(0);
    expect(deployment.poolFraction.value).toBe(0.75);
    expect(deployment.latency).toEqual({ value: null, state: 'no-observations' });
    expect(deployment.decodeSpeed.value).toBeNull();
    expect(metricValues(page('deployment', { ...data, entity: deployment.key }))).toContain('No observations at window end');
  });

  it('a failing GPU exporter masks old GPU values while keeping host memory and board evidence', () => {
    const data = ctx([
      frame('hostUp', [1], { instance: 'spark-1', platform: 'spark' }), frame('gpuUp', [0], { instance: 'spark-1' }),
      frame('memoryAvailable', [8 * 1024 ** 3], { instance: 'spark-1' }), frame('gpuTemp', [65], { instance: 'spark-1' }),
      frame('boardHistory', [89], { instance: 'spark-1' }), frame('gpuTemp', [62], { instance: 'spark-2' }),
    ]);
    const [first, second] = hardware(data);
    expect(first.gpuCollection).toBe('failed');
    expect(first.gpuTemp).toBeNull();
    expect(first.memoryAvailable).toBe(8 * 1024 ** 3);
    expect(first.boardTemp).toBe(89);
    expect(second.gpuCollection).toBe('metrics-only');
    expect(second.hostCollection).toBe('unknown');
    expect(textOf(page('hardware', data))).toContain('Collector status not captured');
    expect(hardware(ctx([]))).toHaveLength(2);
  });

  it('queue/concurrency growth counts head requests once and retains recipe-specific stream capacity', () => {
    const alternate = { ...id, recipe: 'recipe-b' };
    const data = ctx([
      frame('identity', [1], id), frame('identity', [1], { ...id, rank: '1' }), frame('identity', [1], alternate),
      frame('running', [2, 4], id), frame('running', [4], { ...id, rank: '1' }), frame('waiting', [0, 3], id), frame('streamsHistory', [4], id),
      frame('running', [6], alternate), frame('waiting', [1], alternate), frame('streamsHistory', [8], alternate),
    ]);
    const rows = deployments(data);
    expect(rows).toHaveLength(2);
    expect(rows.map((row) => [row.running.value, row.waiting.value, row.streams.value])).toEqual([[4, 3, 4], [6, 1, 8]]);
    expect(rows[0].host).toBe('spark-1');
    // Duplicate unlabeled replicas are ambiguous, never an implicit sum or first-series selection.
    const ambiguous = deployments(ctx([...data.series, frame('running', [9], id)]))[0];
    expect(ambiguous.running.state).toBe('ambiguous');
  });

  it('recipe swaps and counter resets do not reuse identity, create a throughput spike or infer idle', () => {
    const next = { ...id, recipe: 'recipe-b', runtime: 'other-runtime' };
    const data = ctx([
      frame('identity', [1], next), frame('running', [1], next),
      frame('runningHistory', [4, 4], id, [TO - 120000, TO - 60000], 15000),
      frame('aggregateOutput', [90, 95], id, [TO - 120000, TO - 60000], 15000),
      frame('rawGenerationCounter', [100000, 0], next),
      frame('completedOutput', [null], next),
    ]);
    const rows = deployments(data);
    expect(rows).toHaveLength(2);
    expect(rows[0].collection).toBe('unknown');
    expect(rows[0].aggregateOutput.value).toBeNull();
    expect(rows[1].runtime).toBe('other-runtime');
    expect(rows[1].aggregateOutput.value).toBeNull();
    expect(rows[1].completedOutput.value).toBeNull();
    expect(rows[0].key).not.toBe(rows[1].key);
    const query = queries.find((item) => item.key === 'aggregateOutput')!;
    expect(query.expr).toContain('rate(tensorfold_health:completion_tokens_total');
    expect(queries.some((item) => /deriv\(|clamp_min|or vector\(0\)/.test(item.expr))).toBe(false);
    expect(query.capture).toBeUndefined();
  });

  it('unobserved context families and sparse completions stay explicit, without session tracks', () => {
    const data = ctx([
      frame('identity', [1], id), frame('capPool', [0], id), frame('capContext', [0], id), frame('capLatency', [1], id),
      frame('latencyHistory', [14], id, [TO - 120000], 15000),
      frame('ttftHistory', [1, null], id),
    ]);
    const [deployment] = deployments(data);
    expect(deployment.poolFraction.state).toBe('not-observed');
    expect(deployment.contextLength.state).toBe('not-observed');
    expect(deployments({ ...data, series: [...data.series, frame('contextLength', [8192], id)] })[0].contextLength).toEqual({ state: 'observed', value: 8192 });
    expect(deployment.latency).toEqual({ state: 'no-observations', value: null });
    expect(deployment.ttft.value).toBeNull();
    expect(deployment.contexts).toEqual([]);
    expect(textOf(page('deployment', { ...data, entity: deployment.key }))).toContain('Family not observed');
    expect(poolFraction(0, 0)).toBeNull();
    expect(poolFraction(100, 101)).toBeNull();
  });

  it('board heat, thermal windows and RoCE errors stay on the right host and do not bridge a gap', () => {
    const times = [TO - 300000, TO - 240000, TO - 60000, TO];
    const data = ctx([
      frame('gpuTemp', [62], { instance: 'spark-1' }), frame('boardHistory', [92], { instance: 'spark-1' }),
      frame('boardHistory', [74], { instance: 'spark-2' }), frame('clockHistory', [2.5e9], { instance: 'spark-1' }),
      frame('roceTraffic', [284e6], { instance: 'spark-1' }),
      frame('thermalSeconds', [3, 4, 5, 0], { instance: 'spark-1' }, times),
      frame('roceErrors', [0, 2, null, 1], { instance: 'spark-2' }, times),
    ]);
    const [first, second] = hardware(data);
    expect(first.gpuTemp).toBe(62);
    expect(first.boardTemp).toBe(92);
    expect(first.thermalEvents).toHaveLength(2);
    expect(first.roceEvents).toHaveLength(0);
    expect(second.thermalEvents).toHaveLength(0);
    expect(second.roceEvents).toHaveLength(2);
    expect(first.thermalEvents[0].end).toBe(TO - 180000);
    const sections = page('host', { ...data, entity: 'spark-1' });
    expect(metricValues(sections)).toContain('2.5 GHz');
    expect(metricValues(sections)).toContain('284 MB/s');
    expect(sections.filter((section) => section.kind === 'chart').every((section) => section.entityLabels?.includes('instance'))).toBe(true);
    const thermal = eventSpans({ labels: {}, step: 60000, points: [[TO - 120000, 2], [TO - 60000, 3], [TO, 4]] }, TO - 60000, TO);
    expect(thermal).toEqual([{ start: TO - 60000, end: TO, samples: 2, peak: 4 }]);
  });
});

describe('capture, range and navigation boundaries', () => {
  it('loads real bounded captures without borrowing historical identity or pool capacity', () => {
    const data = ctx(captured, { mode: 'recorded' });
    const rows = hardware(data);
    expect(rows.map((row) => row.instance)).toEqual(['spark-1', 'spark-2']);
    expect(rows[0].memoryAvailable).toBe(5248249856);
    expect(rows[0].boardTemp).toBe(91.2);
    expect(rows[0].gpuCollection).toBe('metrics-only');
    const [deployment] = deployments(data);
    expect(deployment.recipe).toContain('durable');
    expect(deployment.runtime).toBe('');
    expect(deployment.running.value).toBeNull();
    expect(deployment.poolFraction.value).toBeNull();
    expect(aggregate(data)).toMatchObject({ running: 2, waiting: 0, requestWindowPercent: 27.177400000000002 });
    expect(availableFrames(data).some((item) => item.refId === ref('contextLength'))).toBe(false);
    expect(page('inference', data).filter((section) => section.kind === 'chart')).toHaveLength(4);
  });

  it('replays all four original anonymous contexts only inside the dated inference window', () => {
    const data = ctx(captured, { mode: 'recorded' }, HISTORY);
    const [deployment] = deployments(data);
    expect(historicalWindow(data)).toBe(true);
    expect(deployment.runtime).toBe('tensorfold');
    expect(deployment.recipe.endsWith('durable')).toBe(false);
    expect(deployment.running.value).toBe(4);
    expect(deployment.waiting.value).toBe(1);
    expect(deployment.streams.value).toBe(4);
    expect(deployment.poolFraction.value).toBeCloseTo(0.4758128469);
    expect(deployment.contextLength.value).toBe(1048576);
    expect(deployment.contexts).toHaveLength(4);
    expect(deployment.contexts[0].fraction).toBeCloseTo(236465.422336 / 1048576);
    expect(deployment.contexts[0]).not.toHaveProperty('pool');
    expect(deployment.contexts[0]).not.toHaveProperty('session');
    expect(deployment.aggregateOutput.value).toBeNull();
    expect(deployment.decodeSpeed.value).toBeCloseTo(21.2272575856);
    expect(deployment.ttft.value).toBeCloseTo(16.6106621111);
    expect(aggregate(data).running).toBeNull();
    expect(hardware(data)[0].memoryAvailable).toBeNull();
    expect(availableFrames(ctx(captured, { mode: 'recorded' }, { from: HISTORY.from - 1, to: HISTORY.to })).some((item) => item.refId === ref('identity'))).toBe(false);
  });

  it('refuses zero-filled aggregate observations without unfilled history corroboration', () => {
    const data = ctx([frame('runningTotal', [0]), frame('waitingTotal', [0]), frame('requestShareTotal', [0]), frame('serving', [1], id)], { mode: 'recorded' });
    expect(aggregate(data)).toMatchObject({ running: null, waiting: null, requestWindowPercent: null });
    expect(aggregate({ ...data, series: [...data.series, frame('waitingTotalHistory', [0])] }).waiting).toBe(0);
    expect(aggregate({ ...data, series: [...data.series, frame('waitingTotalHistory', [2])] }).waiting).toBeNull();
  });

  it('preserves range, source and special-character identity across internal and native links', () => {
    const strange = { ...id, model: 'A & B/2', recipe: 'recipe ?x=1#new', runtime: 'tensorfold+test' };
    const data = ctx([frame('identity', [1], strange)]);
    const [deployment] = deployments(data);
    const table = page('inference', data).find((section) => section.id === 'serving-deployments');
    if (table?.kind !== 'table') throw new Error('Missing identity table');
    const cell = table.rows[0].cells[0];
    if (typeof cell === 'string' || !cell.href) throw new Error('Missing detail link');
    const url = new URL(cell.href);
    expect(url.searchParams.get('entity')).toBe(deploymentKey({ host: strange.instance, model: strange.model, recipe: strange.recipe, runtime: strange.runtime }));
    expect(url.searchParams.get('from')).toBe(String(FROM));
    expect(url.searchParams.get('to')).toBe(String(TO));
    expect(url.searchParams.get('var-source')).toBe('replay');
    const section = page('deployment', { ...data, entity: deployment.key }).find((item) => item.id === 'inference-tools');
    if (section?.kind !== 'links') throw new Error('Missing native links');
    const native = new URL(section.items[0].href);
    expect(native.pathname).toBe('/d/spark-tensorfold-performance');
    expect(native.searchParams.get('var-model')).toBe(strange.model);
    expect(native.searchParams.get('var-recipe')).toBe(strange.recipe);
    expect(native.searchParams.get('var-serving_host')).toBe('spark-1');
    expect(native.searchParams.get('from')).toBe(String(FROM));
    const boundary = page('inference', data)[0];
    if (boundary.kind !== 'text') throw new Error('Missing boundary');
    const history = new URL(boundary.links![0].href);
    expect(history.searchParams.get('from')).toBe(String(HISTORY.from));
    expect(history.searchParams.get('to')).toBe(String(HISTORY.to));
    expect(history.searchParams.get('view')).toBe('inference');
    expect(history.searchParams.get('var-source')).toBe('replay');
    const hostLinks = page('host', { ...data, entity: 'spark-1' }).find((item) => item.id === 'hardware-tools');
    if (hostLinks?.kind !== 'links') throw new Error('Missing host links');
    expect(hostLinks.items.slice(0, 3).every((item) => new URL(item.href).searchParams.get('var-node') === 'spark-1')).toBe(true);
  });

  it('does not substitute a replacement recipe for a selected deployment', () => {
    const data = ctx([frame('identity', [1], { ...id, recipe: 'replacement' })]);
    const selected = deploymentKey({ host: id.instance, model: id.model, recipe: id.recipe, runtime: id.runtime });
    expect(page('deployment', { ...data, entity: selected }).some((section) => section.id === 'deployment-not-found')).toBe(true);
  });

  it('requires one unambiguous endpoint sample and never jumps over a missing final sample', () => {
    expect(atEnd({ labels: {}, step: 60000, points: [[TO - 60000, 9], [TO, null]] }, FROM, TO)).toBeNull();
    expect(atEnd({ labels: {}, step: 60000, points: [[TO - 300000, 9]] }, FROM, TO)).toBeNull();
  });

  it('declares traceable unique queries without samples or reset-sensitive derivative fallbacks', () => {
    expect(new Set(queries.map((query) => query.refId)).size).toBe(queries.length);
    for (const query of queries) {
      expect(query.refId.startsWith('D_spark_')).toBe(true);
      expect(query).not.toHaveProperty('series');
      expect(query).not.toHaveProperty('samples');
      expect(query.instant).not.toBe(query.range);
      const proof = evidence.queries[query.refId as keyof typeof evidence.queries];
      expect(proof).toBeDefined();
      if (query.capture?.kind === 'inference') {
        expect('capturedExpression' in proof).toBe(true);
        if ('capturedExpression' in proof) expect(hash(proof.capturedExpression)).toBe(query.capture.key);
      }
    }
    for (const name of ['hardware', 'inference', 'host', 'deployment']) {
      const sections = page(name, ctx(captured, { mode: 'recorded' }));
      expect(new Set(sections.map((section) => section.id)).size).toBe(sections.length);
      for (const section of sections) if (section.kind === 'chart') {
        expect(section.refs.every((key) => queries.some((query) => query.refId === key && query.range))).toBe(true);
      }
    }
  });
});
