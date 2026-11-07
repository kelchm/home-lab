import type { TimeRange } from '@grafana/data';
import { describe, expect, it } from 'vitest';
import type { DomainContext } from './contract';
import type { FrameLike, Series } from '../model/semantics';
import { current, records } from './data';

const ctx = { timeRange: { from: { valueOf: () => 0 }, to: { valueOf: () => 600000 } } as unknown as TimeRange } satisfies Pick<DomainContext, 'timeRange'>;

describe('Domain observations', () => {
  it('does not inspect unrelated frames while preserving observed cadence and all matching fields', () => {
    const unrelated: FrameLike = { refId: 'other', get fields(): unknown[] { throw new Error('Unrelated frame was inspected'); } };
    const frame: FrameLike = { refId: 'wanted', fields: [
      { type: 'time', values: [0, 60000, 120000], config: { interval: 300000 } },
      { type: 'number', labels: { host: 'one' }, values: [0, null, 2] },
      { type: 'number', labels: { host: 'two' }, values: [3, 4, 5] },
    ] };
    expect(records([unrelated, frame], 'wanted')).toEqual([
      { labels: { host: 'one' }, step: 60000, points: [[0, 0], [60000, null], [120000, 2]] },
      { labels: { host: 'two' }, step: 60000, points: [[0, 3], [60000, 4], [120000, 5]] },
    ]);
    expect(records([unrelated], 'missing')).toEqual([]);
  });

  it.each([
    { points: [[540000, 0]], step: null, expected: 0 },
    { points: [[539999, 1]], step: null, expected: null },
    { points: [[420000, 1]], step: 120000, expected: 1 },
    { points: [[419999, 1]], step: 120000, expected: null },
    { points: [[600000, null], [540000, 1]], step: 60000, expected: null },
    { points: [[540000, 0], [600001, 1]], step: 60000, expected: 0 },
    { points: [[-1, 1]], step: 600000, expected: null },
    { points: [[600000, Infinity]], step: null, expected: null },
    { points: [[600000, -1]], step: null, expected: -1 },
  ] as Array<{ points: Series['points']; step: number | null; expected: number | null }>)('bounds the last in-window query evaluation: $points / $step', ({ points, step, expected }) => {
    expect(current(ctx, { labels: {}, points, step })).toBe(expected);
  });
});
