import { readFileSync } from 'node:fs';
import type { DataFrame, PanelData, TimeRange } from '@grafana/data';

export const FROM = 1793496424636;
export const TO = 1793582824636;
/** What a host requests. Replay frames keep their recorded cadence, so this is deliberately wrong for most of them. */
export const REQUESTED_STEP = 300000;

interface Entry {
  refId: string;
  expr: string;
  instant: boolean;
  scope: string;
  series: Array<{ labels: Record<string, string>; t: number[]; v: Array<number | null> }>;
}

// Read-only inputs, relative to shared/, where the tests run.
export const catalog: Entry[] = JSON.parse(readFileSync('../runtime/catalog.json', 'utf8'));
export const snapshot = JSON.parse(readFileSync('../fixtures/reference/overview-snapshot.json', 'utf8'));

export const frame = (refId: string, values: number[], labels: Record<string, string> = {}, times = values.map((_, i) => TO - (values.length - 1 - i) * 60000), interval = REQUESTED_STEP) =>
  ({
    refId,
    length: values.length,
    fields: [
      { name: 'Time', type: 'time', config: { interval }, values: times },
      { name: 'Value', type: 'number', labels, config: {}, values },
    ],
  }) as unknown as DataFrame;

export const emptyFrame = (refId: string) => ({ refId, length: 0, fields: [] }) as unknown as DataFrame;

const edge = (ms: number) => ({ valueOf: () => ms });

export const range = (from: string | number = FROM, to: string | number = TO, values: [number, number] = [FROM, TO]) =>
  ({ from: edge(values[0]), to: edge(values[1]), raw: { from, to } }) as unknown as TimeRange;

/** A catalog entry as Grafana delivers it: one frame per series, and one empty frame for a successful empty answer. */
export function framesOf(entry: Entry): DataFrame[] {
  const frames = entry.series.flatMap((s) => {
    const points = s.t.map((t, i) => [t, s.v[i]] as const).filter(([, v]) => v != null);
    const kept = entry.instant ? points.slice(-1) : points;
    return kept.length ? [frame(entry.refId, kept.map((p) => p[1]!), s.labels, kept.map((p) => p[0]))] : [];
  });
  return frames.length ? frames : [emptyFrame(entry.refId)];
}

export const allFrames = (scopes = ['common', 'network']) => catalog.filter((e) => scopes.includes(e.scope)).flatMap(framesOf);

const targets = (scopes: string[]) => catalog.filter((e) => scopes.includes(e.scope)).map((e) => ({ refId: e.refId, expr: e.expr, datasource: { uid: 'comparison', type: 'prometheus' } }));

export const panelData = (over: Partial<PanelData> & { scopes?: string[] } = {}): PanelData => {
  const { scopes = ['common', 'network'], ...rest } = over;
  return { state: 'Done', series: allFrames(scopes), timeRange: range(), request: { targets: targets(scopes) }, ...rest } as unknown as PanelData;
};

/** A request naming only its source panel, which is all a Dashboard-datasource consumer sees. */
export const sharedRequest = { targets: [{ refId: 'Shared', panelId: 1, datasource: { uid: '-- Dashboard --' } }] } as unknown as PanelData['request'];
