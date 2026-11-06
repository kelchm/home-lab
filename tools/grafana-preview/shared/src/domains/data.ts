import type { FrameLike, Series } from '../model/semantics';
import { instant, seriesOf } from '../model/semantics';
import { measured } from '../model/adapter';

export function records(frames: readonly FrameLike[], ref: string): Series[] {
  return seriesOf(measured(frames), ref);
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

export function byLabels(frames: readonly FrameLike[], ref: string, names: string[]): Map<string, Series> {
  return new Map(records(frames, ref).map((s) => [names.map((name) => s.labels[name] ?? '').join('|'), s]));
}
