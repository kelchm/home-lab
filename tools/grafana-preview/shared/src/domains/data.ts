import type { FrameLike, Series } from '../model/semantics';
import { instant, seriesOf } from '../model/semantics';
import { measured } from '../model/adapter';
import type { DomainContext } from './contract';

export function records(frames: readonly FrameLike[], ref: string): Series[] {
  return seriesOf(measured(frames.filter((frame) => frame.refId === ref)), ref);
}

/** Bounds query evaluation timestamps with a one-minute floor; it cannot establish underlying source sample age. */
export function current(ctx: Pick<DomainContext, 'timeRange'>, series: Series | undefined): number | null {
  if (!series) return null;
  const from = ctx.timeRange.from.valueOf(), to = ctx.timeRange.to.valueOf();
  const point = series.points.filter(([at]) => at >= from && at <= to).sort((a, b) => a[0] - b[0]).at(-1);
  return point && to - point[0] <= Math.max(60000, (series.step ?? 0) * 1.5) && point[1] != null && Number.isFinite(point[1]) ? point[1] : null;
}

export function value(frames: readonly FrameLike[], ref: string, labels: Record<string, string> = {}): number | null {
  const found = records(frames, ref).filter((s) => Object.entries(labels).every(([key, item]) => s.labels[key] === item));
  return found.length === 1 ? instant(found[0]) : null;
}

export function labelValue(record: Series | undefined): number | null {
  return instant(record);
}

export function format(value: number | null | undefined, digits = 1, suffix = ''): string {
  return value == null || !Number.isFinite(value) ? 'Unknown' : `${new Intl.NumberFormat('en-US', { maximumFractionDigits: digits }).format(value)}${suffix}`;
}
