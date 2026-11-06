import type { DomainContext, DomainQuery } from '../contract';
import { records } from '../data';
import type { FrameLike, Series } from '../../model/semantics';
import manifest from './queries.json';

export const queries = manifest as DomainQuery[];
export const ref = (key: string) => `D_spark_${key}`;
export const HISTORY = { from: 1793487600000, to: 1793491500000 };
export const HISTORY_LABEL = 'Oct 31, 2026 23:00 – Nov 1, 2026 00:05 UTC';
const captures = new Map(queries.map((query) => [query.refId, query.capture]));
const bounds = (ctx: Pick<DomainContext, 'timeRange'>) => ({ from: ctx.timeRange.from.valueOf(), to: ctx.timeRange.to.valueOf() });

export function historicalWindow(ctx: Pick<DomainContext, 'timeRange'>): boolean {
  const { from, to } = bounds(ctx);
  return from >= HISTORY.from && to <= HISTORY.to && from < to;
}

/** A second guard against a runtime accidentally returning the other recording's frames. */
export function availableFrames(ctx: DomainContext): FrameLike[] {
  return ctx.series.filter((frame) => {
    if (ctx.mode === 'live') return true;
    const capture = captures.get(frame.refId ?? '');
    if (!capture) return false;
    return capture.kind === 'inference' ? historicalWindow(ctx) : !historicalWindow(ctx);
  });
}

/** Do not backfill the end of a window from an earlier non-null sample or cross a collection gap. */
export function atEnd(series: Series | undefined, from: number, to: number): number | null {
  if (!series) return null;
  const point = series.points.filter(([at]) => at >= from && at <= to).sort((a, b) => a[0] - b[0]).at(-1);
  const tolerance = Math.max(0, (series.step ?? 0) * 1.5);
  return point && to - point[0] <= tolerance && point[1] != null && Number.isFinite(point[1]) ? point[1] : null;
}

function one(list: Series[], ctx: DomainContext): number | null {
  const { from, to } = bounds(ctx);
  return list.length === 1 ? atEnd(list[0], from, to) : null;
}

function hostSeries(frames: FrameLike[], key: string, host: string): Series[] {
  return records(frames, ref(key)).filter(({ labels }) => labels.instance === host && (!labels.platform || labels.platform === 'spark'));
}

export type CollectorState = 'observed' | 'failed' | 'metrics-only' | 'unknown';
export interface Host {
  instance: string;
  hostCollection: CollectorState;
  gpuCollection: CollectorState;
  memoryAvailable: number | null;
  cpu: number | null;
  diskUsed: number | null;
  gpuUtil: number | null;
  gpuTemp: number | null;
  gpuMargin: number | null;
  gpuPower: number | null;
  boardTemp: number | null;
  clockHz: number | null;
  roceBytes: number | null;
  memoryStalls: number | null;
  thermalSeconds: number | null;
  roceErrors: number | null;
  thermalEvents: EventSpan[];
  roceEvents: EventSpan[];
}

const collector = (up: number | null, hasMetrics: boolean): CollectorState => up === 0 ? 'failed' : up === 1 ? 'observed' : hasMetrics ? 'metrics-only' : 'unknown';

export function hardware(ctx: DomainContext): Host[] {
  const frames = availableFrames(ctx);
  const names = new Set(['spark-1', 'spark-2']);
  for (const key of ['hostUp', 'memoryAvailable', 'gpuTemp', 'boardHistory']) {
    for (const { labels } of records(frames, ref(key))) if (labels.instance && (!labels.platform || labels.platform === 'spark')) names.add(labels.instance);
  }
  return [...names].sort().map((instance) => {
    const get = (key: string) => one(hostSeries(frames, key, instance), ctx);
    const hostUp = get('hostUp');
    const gpuUp = get('gpuUp');
    const gpu = (key: string) => gpuUp === 0 ? null : get(key);
    const node = (key: string) => hostUp === 0 ? null : get(key);
    const spans = (key: string) => hostSeries(frames, key, instance).flatMap((series) => eventSpans(series, bounds(ctx).from, bounds(ctx).to));
    return {
      instance,
      hostCollection: collector(hostUp, get('memoryAvailable') != null),
      gpuCollection: collector(gpuUp, get('gpuTemp') != null || get('gpuUtil') != null),
      memoryAvailable: node('memoryAvailable'), cpu: node('hostCpu'), diskUsed: node('diskUsed'),
      gpuUtil: gpu('gpuUtil'), gpuTemp: gpu('gpuTemp'), gpuMargin: gpu('gpuMargin'), gpuPower: gpu('gpuPower'),
      boardTemp: node('boardHistory'), clockHz: gpu('clockHistory'), roceBytes: node('roceTraffic'),
      memoryStalls: node('memoryStalls'), thermalSeconds: gpu('thermalSeconds'), roceErrors: node('roceErrors'),
      thermalEvents: spans('thermalSeconds'), roceEvents: spans('roceErrors'),
    };
  });
}

export interface EventSpan { start: number; end: number; samples: number; peak: number }

/** Positive sampled windows, not an inferred incident duration. Nulls and absent steps break a span. */
export function eventSpans(series: Series, from: number, to: number): EventSpan[] {
  const out: EventSpan[] = [];
  const step = series.step ?? 0;
  let active: EventSpan | undefined;
  let previous: number | undefined;
  for (const [at, value] of [...series.points].sort((a, b) => a[0] - b[0])) {
    if (at < from || at > to) continue;
    if (previous != null && (!step || at - previous > step * 1.5)) active = undefined;
    previous = at;
    if (value == null || !Number.isFinite(value) || value <= 0) { active = undefined; continue; }
    if (!active) { active = { start: Math.max(from, at), end: at, samples: 0, peak: value }; out.push(active); }
    active.end = Math.min(to, at + step);
    active.samples++;
    active.peak = Math.max(active.peak, value);
  }
  return out;
}

export interface Identity { host: string; model: string; recipe: string; runtime: string }
export const deploymentKey = (id: Identity): string => JSON.stringify([id.host, id.model, id.recipe, id.runtime]);
export function identity(labels: Record<string, string>): Identity | null {
  return labels.instance && labels.model && labels.recipe ? { host: labels.instance, model: labels.model, recipe: labels.recipe, runtime: labels.runtime ?? '' } : null;
}
const matches = (labels: Record<string, string>, id: Identity) => {
  const found = identity(labels);
  // The exporter scrapes the head rank only. Never sum TP workers into requests or deployment capacity.
  const rank = labels.rank ?? labels.sparkrun_rank;
  return found != null && deploymentKey(found) === deploymentKey(id) && (rank == null || rank === '0');
};

export type SignalState = 'observed' | 'unsupported' | 'unavailable' | 'no-observations' | 'ambiguous';
export interface Signal { value: number | null; state: SignalState }
const absent = (state: SignalState = 'unavailable'): Signal => ({ value: null, state });
const observed = (value: number | null): Signal => value == null || !Number.isFinite(value) ? absent() : { value, state: 'observed' };

export interface Deployment extends Identity {
  key: string;
  collection: CollectorState;
  running: Signal;
  waiting: Signal;
  streams: Signal;
  poolFraction: Signal;
  poolTokens: Signal;
  poolFree: Signal;
  contextLength: Signal;
  contexts: Array<{ rank: number; tokens: number; fraction: number | null }>;
  decodeSpeed: Signal;
  ttft: Signal;
  latency: Signal;
  cacheFraction: Signal;
  aggregateOutput: Signal;
  completedOutput: Signal;
  healthReported: Signal;
}

export function poolFraction(total: number | null, free: number | null): number | null {
  return total != null && free != null && total > 0 && free >= 0 && free <= total ? 1 - free / total : null;
}

export function deployments(ctx: DomainContext): Deployment[] {
  const frames = availableFrames(ctx);
  const daily = ctx.mode === 'recorded' && !historicalWindow(ctx);
  const identities = new Map<string, Identity>();
  const identityRefs = daily ? ['serving'] : ['identity', 'runningHistory', 'waitingHistory', 'poolHistory', ...(ctx.mode === 'live' ? ['serving'] : [])];
  for (const key of identityRefs) for (const series of records(frames, ref(key))) {
    const id = identity(series.labels);
    const { from, to } = bounds(ctx);
    if (id && matches(series.labels, id) && series.points.some(([at, value]) => at >= from && at <= to && value != null)) identities.set(deploymentKey(id), id);
  }
  return [...identities.values()].map((id) => {
    const list = (key: string) => records(frames, ref(key)).filter((series) => matches(series.labels, id));
    const read = (key: string, capability?: string): Signal => {
      const found = list(key);
      if (found.length > 1) return absent('ambiguous');
      const capabilityValue = capability ? one(list(capability), ctx) : null;
      if (capabilityValue === 0) return absent('unsupported');
      const value = one(found, ctx);
      if (value != null && value >= 0) return observed(value);
      return absent(capabilityValue != null && capabilityValue > 0 || found.length > 0 ? 'no-observations' : 'unavailable');
    };
    const reachable = one(list(daily ? 'serving' : 'identity'), ctx);
    const failed = one(list('serving'), ctx) === 0;
    const collection: CollectorState = failed ? 'failed' : reachable === 1 ? 'observed' : 'unknown';
    const measure = (key: string, capability?: string): Signal => collection === 'observed' ? read(key, capability) : absent();
    const total = measure('poolTokens', 'capPool');
    const free = measure('poolFree', 'capPool');
    const ratio = total.value != null && free.value != null ? observed(poolFraction(total.value, free.value)) : measure('poolRatio', 'capPool');
    const length = measure('contextLength', 'capContext');
    const { from, to } = bounds(ctx);
    const contexts = collection === 'observed' ? list('contextTokens')
      .map((series) => atEnd(series, from, to)).filter((v): v is number => v != null && v >= 0)
      .sort((a, b) => b - a).map((tokens, index) => ({ rank: index + 1, tokens, fraction: length.value != null && length.value > 0 ? tokens / length.value : null })) : [];
    return {
      ...id, key: deploymentKey(id), collection,
      running: measure('running'), waiting: measure('waiting'), streams: measure('streamsHistory'),
      poolFraction: ratio, poolTokens: total, poolFree: free, contextLength: length, contexts,
      decodeSpeed: measure('decodeSpeed', 'capDecode'), ttft: measure('ttftHistory', 'capLatency'), latency: measure('latencyHistory', 'capLatency'),
      cacheFraction: measure('cacheFraction'), aggregateOutput: measure('aggregateOutput', 'capOutput'), completedOutput: measure('completedOutput'),
      healthReported: measure('healthReported'),
    };
  }).sort((a, b) => a.host.localeCompare(b.host) || a.recipe.localeCompare(b.recipe) || a.runtime.localeCompare(b.runtime));
}

/** The daily recording discarded identity. Even a sole current target doesn't recover historical attribution. */
export function aggregate(ctx: DomainContext) {
  const frames = availableFrames(ctx);
  const scalar = (key: string) => one(records(frames, ref(key)).filter((series) => Object.keys(series.labels).length === 0), ctx);
  const corroborated = (key: string, history: string) => {
    const value = scalar(key);
    if (value === 0) return scalar(history) === 0 ? 0 : null;
    return value;
  };
  return {
    running: corroborated('runningTotal', 'runningTotalHistory'), waiting: corroborated('waitingTotal', 'waitingTotalHistory'),
    requestWindowPercent: scalar('requestShareTotal') === 0 ? null : scalar('requestShareTotal'),
    completedOutput: scalar('completedHistory'), promptInput: scalar('promptHistory'),
  };
}

export const scale = (value: number | null, divisor: number) => value == null ? null : value / divisor;
