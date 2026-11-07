// Turns root's fixture catalog into frames shaped like the Prometheus datasource's, for tests and the browser harness. Nothing here is bundled into the app.
import { type DataFrame, type DataQueryRequest, FieldType, toDataFrame } from '@grafana/data';

import catalog from '../../../runtime/catalog.json';

interface Entry {
  refId: string;
  series: Array<{ labels: Record<string, string>; t: number[]; v: Array<number | null>; step?: number }>;
}

const entries = new Map((catalog as unknown as Entry[]).map((entry) => [entry.refId, entry]));

function framesFor(refId: string, instant: boolean, from: number, to: number): DataFrame[] {
  return (entries.get(refId)?.series ?? []).flatMap((series) => {
    // Use the full captured timeline before clipping or removing nulls, so a gap never changes its own cadence.
    const deltas = series.t.slice(1).map((t, index) => t - series.t[index]).filter((delta) => delta > 0 && Number.isFinite(delta));
    const interval = series.step != null && series.step > 0 ? series.step : deltas.length ? Math.min(...deltas) : undefined;
    const points = series.t.map((t, index) => [t, series.v[index]] as const).filter(([t, v]) => v != null && (instant ? t <= to : t >= from && t <= to));
    const kept = instant ? points.slice(-1) : points;
    if (!kept.length) {
      return [];
    }
    return [
      toDataFrame({
        refId,
        fields: [
          { name: 'Time', type: FieldType.time, values: kept.map(([t]) => t), config: { ...(interval == null ? {} : { interval }) } },
          { name: 'Value', type: FieldType.number, values: kept.map(([, v]) => v), labels: series.labels },
        ],
      }),
    ];
  });
}

export const recordedFrames = (request: DataQueryRequest) => {
  const [from, to] = [request.range.from.valueOf(), request.range.to.valueOf()];
  return request.targets.flatMap((target) => framesFor(target.refId, Boolean((target as { instant?: boolean }).instant), from, to));
};
