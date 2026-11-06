import { smallestStep, type DataLike, type FrameLike, type Point } from './semantics';

interface FieldLike {
  type?: string;
  values?: ArrayLike<unknown>;
  config?: { interval?: number };
}

const SAMPLES_TO_TRUST_CADENCE = 3;

/**
 * A replayed series keeps its recorded timestamps whatever step was requested, so a 1 min series can arrive labelled 5 min and a
 * 15 min one the reverse. The model places failure spans and gaps by step, so the observed cadence wins once there are enough
 * samples to establish one. Two samples can't: they may straddle a hole, so those keep the reported step.
 */
function withObservedStep(frame: FrameLike): FrameLike {
  const fields = frame.fields as FieldLike[];
  const time = fields.find((field) => field.type === 'time');
  const times = Array.from(time?.values ?? []) as number[];
  if (times.length < SAMPLES_TO_TRUST_CADENCE) {
    return frame;
  }
  const observed = smallestStep(times.map((t): Point => [t, null]));
  if (observed == null) {
    return frame;
  }
  return { ...frame, fields: fields.map((field) => (field === time ? { ...field, config: { ...field.config, interval: observed } } : field)) };
}

/** Everything the model reads, with this surface's prefix taken off, so the reviewed code sees the refIds it was written for. */
export function scoped(series: readonly FrameLike[], prefix: string): DataLike {
  return { series: series.filter((frame) => frame.refId?.startsWith(prefix)).map((frame) => ({ ...withObservedStep(frame), refId: frame.refId?.slice(prefix.length) })) };
}

export const measured = (series: readonly FrameLike[]): DataLike => ({ series: series.map(withObservedStep) });

/** A value for every moment all inputs share. Anything missing leaves a gap: nothing here interpolates across one. */
export function combine(list: Point[][], fn: (row: number[]) => number): Point[] {
  if (!list.length || list.some((points) => !points.length)) {
    return [];
  }
  const lookups = list.map((points) => new Map(points));
  const times = [...new Set(list.flatMap((points) => points.map((point) => point[0])))].sort((a, b) => a - b);
  return times.map((t) => {
    const row = lookups.map((lookup) => lookup.get(t));
    return [t, row.every((v) => v != null) ? fn(row as number[]) : null];
  });
}

export const scale = (points: Point[], fn: (value: number) => number): Point[] => points.map(([t, v]) => [t, v == null ? null : fn(v)]);
