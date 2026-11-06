import { dateTime, type DataQueryRequest, FieldType } from '@grafana/data';
import { describe, expect, it } from 'vitest';
import { recordedFrames } from './recorded';

const end = 1793582824636;
const frames = (refId: string, instant = false, from = end - 86400000, to = end) => recordedFrames({ targets: [{ refId, instant }], range: { from: dateTime(from), to: dateTime(to) } } as unknown as DataQueryRequest);

describe('recorded boundary cadence', () => {
  it('keeps minute and quarter-hour capture cadences even when the displayed range holds only its endpoint', () => {
    for (const [ref, interval, at] of [['H_PH', 60000, 1793582820000], ['M_E', 900000, 1793582100000]] as const) {
      const result = frames(ref, false, at, at);
      expect(result.length).toBeGreaterThan(0);
      for (const frame of result) {
        const time = frame.fields.find((field) => field.type === FieldType.time)!;
        expect(time.config.interval).toBe(interval);
        expect(Array.from(time.values)).toEqual([at]);
      }
    }
  });

  it('does not invent a cadence for an instant-only capture or a value for an uncaptured query', () => {
    const result = frames('I_net_availability', true);
    expect(result.length).toBeGreaterThan(0);
    expect(result[0].fields.find((field) => field.type === FieldType.time)?.config.interval).toBeUndefined();
    expect(frames('uncaptured')).toEqual([]);
  });
});
