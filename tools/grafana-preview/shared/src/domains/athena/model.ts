import type { DomainContext, DomainQuery } from '../contract';
import type { FrameLike, Point, Series } from '../../model/semantics';
import { records } from '../data';
import manifest from './queries.json';

export const queries = manifest as DomainQuery[];
export const ref = (key: string) => `D_nas_${key}`;
export const PROBE = 'athena-dsm';
export const DSM_TARGET = 'https://athena.home.kelch.io:5001/';
export type Context = Pick<DomainContext, 'series' | 'timeRange'>;

/** Only the unambiguous observation at the end of this window can support an endpoint claim. */
export function atEnd(series: Series | undefined, ctx: Context): number | null {
  const end = ctx.timeRange.to.valueOf();
  const from = ctx.timeRange.from.valueOf();
  const point = series?.points.filter(([t]) => t >= from && t <= end).at(-1);
  if (!point || point[1] == null || end - point[0] > Math.max(180000, (series?.step ?? 0) * 1.5)) return null;
  return point[1];
}

export function signal(ctx: Context, key: string, labels: Record<string, string> = {}): number | null {
  const found = records(ctx.series, ref(key)).filter((s) => Object.entries(labels).every(([name, value]) => s.labels[name] === value));
  return found.length === 1 ? atEnd(found[0], ctx) : null;
}

export interface Assessment { text: string; tone: 'good' | 'warning' | 'bad' | 'unknown' | 'neutral' }
const unknown: Assessment = { text: 'Unknown', tone: 'unknown' };
export const health = (code: number | null): Assessment => code === 1 ? { text: 'Reported healthy', tone: 'good' }
  : code === 0 ? { text: 'Reported failure', tone: 'bad' } : code === 2 ? { text: 'DSM warning', tone: 'warning' } : unknown;
export const diskStatus = (code: number | null): Assessment => {
  const names: Record<number, string> = { 1: 'Normal', 2: 'Initialized', 3: 'Not initialized', 4: 'System partition failed', 5: 'Crashed' };
  return code != null && names[code] ? { text: names[code], tone: code >= 4 ? 'bad' : code === 3 ? 'neutral' : 'good' } : unknown;
};
export const raidStatus = (code: number | null): Assessment => {
  const names = ['Normal', 'Repairing', 'Migrating', 'Expanding', 'Deleting', 'Creating', 'Syncing', 'Parity check', 'Assembling', 'Cancelling', 'Degraded', 'Crashed'];
  return code != null && Number.isInteger(code) && names[code - 1] ? { text: names[code - 1], tone: code >= 11 ? 'bad' : code === 1 ? 'good' : 'warning' } : unknown;
};
export const assertion = (code: number | null): Assessment => code === 1 ? { text: 'Assertion passing', tone: 'good' }
  : code === 0 ? { text: 'Assertion failed', tone: 'bad' } : unknown;
export const hardwareStatus = (code: number | null): Assessment => code === 1 ? { text: 'Reported normal', tone: 'good' }
  : code === 2 ? { text: 'Reported failure', tone: 'bad' } : unknown;

export interface Drive {
  id: string;
  model: string | null;
  health: Assessment;
  status: Assessment;
  temperature: number | null;
  capacity: number | null;
  reallocated: number | null;
  pending: number | null;
  uncorrectable: number | null;
}
const driveKeys = ['diskInfo', 'diskHealth', 'diskStatus', 'diskTemp', 'diskReallocated', 'diskPending', 'diskUncorrectable'];
const observedInWindow = (ctx: Context, series: Series) => series.points.some(([at, value]) => at >= ctx.timeRange.from.valueOf() && at <= ctx.timeRange.to.valueOf() && value != null);
export function drives(ctx: Context): Drive[] {
  const ids = [...new Set(driveKeys.flatMap((key) => records(ctx.series, ref(key)).filter((s) => observedInWindow(ctx, s)).map((s) => s.labels.disk).filter(Boolean)))];
  return ids.sort((a, b) => a.localeCompare(b, 'en', { numeric: true })).map((id) => {
    const labels = { disk: id };
    const info = records(ctx.series, ref('diskInfo')).filter((s) => s.labels.disk === id && atEnd(s, ctx) != null);
    return {
      id, model: info.length === 1 ? info[0].labels.model_name || null : null,
      health: health(signal(ctx, 'diskHealth', labels)), status: diskStatus(signal(ctx, 'diskStatus', labels)),
      temperature: signal(ctx, 'diskTemp', labels), capacity: null,
      reallocated: signal(ctx, 'diskReallocated', labels), pending: signal(ctx, 'diskPending', labels), uncorrectable: signal(ctx, 'diskUncorrectable', labels),
    };
  });
}

export interface Volume { id: string; usedPercent: number | null; total: number | null; free: number | null }
export function volumes(ctx: Context): Volume[] {
  const ids = [...new Set(['volumes', 'volumeTotal', 'volumeFree'].flatMap((key) => records(ctx.series, ref(key)).filter((s) => observedInWindow(ctx, s)).map((s) => s.labels.raidName).filter(Boolean)))];
  return ids.sort((a, b) => a.localeCompare(b, 'en', { numeric: true })).map((id) => {
    const total = signal(ctx, 'volumeTotal', { raidName: id });
    const free = signal(ctx, 'volumeFree', { raidName: id });
    const percent = signal(ctx, 'volumes', { raidName: id });
    return {
      id, total: total != null && total > 0 ? total : null,
      free: free != null && free >= 0 && (total == null || free <= total) ? free : null,
      usedPercent: percent != null && percent >= 0 && percent <= 100 ? percent : null,
    };
  });
}

export function points(ctx: Context, key: string, labels: Record<string, string> = {}): Series | undefined {
  const candidates = records(ctx.series, ref(key)).filter((s) => Object.entries(labels).every(([name, value]) => s.labels[name] === value));
  if (candidates.length !== 1) return undefined;
  return { ...candidates[0], points: candidates[0].points.filter(([t]) => t >= ctx.timeRange.from.valueOf() && t <= ctx.timeRange.to.valueOf()) };
}

/** The capture establishes sampled continuity, never collector membership or continuous NAS availability. */
export function continuous(series: Series | undefined, from: number, to: number, accepts: (value: number) => boolean): boolean {
  if (!series?.step || to <= from) return false;
  const step = series.step;
  const found = series.points.filter(([t]) => t >= from && t <= to).sort((a, b) => a[0] - b[0]);
  const start = series.points.filter(([t]) => t >= from - step && t <= from).sort((a, b) => a[0] - b[0]).at(-1);
  const end = series.points.filter(([t]) => t >= to && t <= to + step).sort((a, b) => a[0] - b[0])[0];
  return found.length > 0 && start != null && end != null
    && found[0][0] - start[0] <= step * 1.5 && end[0] - found.at(-1)![0] <= step * 1.5
    && found.every(([t, v], i) => v != null && accepts(v) && (!i || t - found[i - 1][0] <= step * 1.5));
}

export interface Incident {
  start: number; end: number; duration: number;
  resolved: boolean; clipped: boolean; startUncertain: boolean; http404: boolean; collectorContinuity: boolean;
}
export function incidents(ctx: Context): Incident[] {
  const history = points(ctx, 'assertionHistory', { probe: PROBE });
  if (!history?.step) return [];
  const step = history.step;
  const result: Incident[] = [];
  let start: number | null = null;
  let last: Point | undefined;
  let clipped = false;
  let startUncertain = false;
  const finish = (end: number, resolved: boolean) => {
    if (start == null) return;
    const http = points(ctx, 'dsmStatus');
    const responseSamples = http?.points.filter(([t]) => t >= start! && t < end) ?? [];
    result.push({
      start, end, duration: end - start, resolved,
      clipped, startUncertain,
      http404: responseSamples.length > 0 && responseSamples.every(([, v]) => v === 404),
      collectorContinuity: continuous(points(ctx, 'scrapes'), start, end, (v) => v === 1),
    });
    start = null;
  };
  for (const point of history.points) {
    const [t, v] = point;
    if (last && (t - last[0] > step * 1.5 || v == null || (v !== 0 && v !== 1))) finish(last[0] + step, false);
    if (v === 0 && start == null) {
      start = t;
      clipped = !last && t <= ctx.timeRange.from.valueOf() + step;
      startUncertain = !last || last[1] !== 1 || t - last[0] > step * 1.5;
    }
    if (v === 1) finish(t, true);
    last = point;
  }
  if (last) finish(Math.min(ctx.timeRange.to.valueOf(), last[0] + step), false);
  return result;
}

/** Optional capture-less families stay unavailable in replay even if a caller accidentally supplies live frames. */
export function domainContext(ctx: DomainContext): DomainContext {
  if (ctx.mode === 'live') return ctx;
  const captured = new Set(queries.filter((query) => query.capture).map((query) => query.refId));
  return { ...ctx, series: ctx.series.filter((frame: FrameLike) => captured.has(frame.refId ?? '')) };
}
