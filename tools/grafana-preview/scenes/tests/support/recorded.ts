// Turns root's fixture catalog into frames shaped like the Prometheus datasource's, for tests and the browser harness. Nothing here is bundled into the app.
import { type DataFrame, type DataQueryRequest, FieldType, toDataFrame } from '@grafana/data';

import catalog from '../../../runtime/catalog.json';

interface Entry {
  refId: string;
  series: Array<{ labels: Record<string, string>; t: number[]; v: Array<number | null> }>;
}

const entries = new Map((catalog as unknown as Entry[]).map((entry) => [entry.refId, entry]));

function framesFor(refId: string, instant: boolean, from: number, to: number): DataFrame[] {
  return (entries.get(refId)?.series ?? []).flatMap((series) => {
    const points = series.t.map((t, index) => [t, series.v[index]] as const).filter(([t, v]) => v != null && (instant ? t <= to : t >= from && t <= to));
    const kept = instant ? points.slice(-1) : points;
    if (!kept.length) {
      return [];
    }
    return [
      toDataFrame({
        refId,
        fields: [
          { name: 'Time', type: FieldType.time, values: kept.map(([t]) => t), config: { interval: 300000 } },
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
