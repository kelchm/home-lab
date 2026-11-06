import type { DomainContext, DomainQuery } from '../contract';
import { records } from '../data';
import manifest from './queries.json';

export const queries = manifest as DomainQuery[];
export const ref = (key: string) => `D_power_${key}`;
export const UNITS = [
  { id: 'compute-rack', name: 'Compute rack', collector: 'ups-compute-rack', source: 'NUT · supporting Pi' },
  { id: 'network-rack', name: 'Network rack', collector: 'nas', source: 'Synology SNMP · Athena' },
  { id: 'office', name: 'Office', collector: 'ups-office', source: 'NUT · supporting Pi' },
] as const;

/** A unique current observation is required; never fill a missing end sample from history. */
export function reading(ctx: DomainContext, key: string, ups: string): number | null {
  const query = queries.find((item) => item.key === key);
  if (ctx.mode === 'recorded' && !query?.capture) return null;
  const list = records(ctx.series, ref(key)).filter((item) => item.labels.ups === ups);
  if (list.length !== 1) return null;
  const from = ctx.timeRange.from.valueOf(), to = ctx.timeRange.to.valueOf();
  const series = list[0];
  const point = series.points.filter(([at]) => at >= from && at <= to).sort((a, b) => a[0] - b[0]).at(-1);
  return point && to - point[0] <= Math.max(60000, (series.step ?? 0) * 1.5) && point[1] != null && Number.isFinite(point[1]) && point[1] >= 0 ? point[1] : null;
}

export type InputState = 'battery' | 'mains' | 'not-asserted' | 'conflicting' | 'unknown';
export function inputState(onBattery: number | null, online: number | null): InputState {
  if (onBattery === 1 && online === 1) return 'conflicting';
  if (onBattery === 1) return 'battery';
  if (onBattery === 0 && online === 1) return 'mains';
  return onBattery === 0 ? 'not-asserted' : 'unknown';
}

export function units(ctx: DomainContext) {
  return UNITS.map((unit) => {
    const load = reading(ctx, 'load', unit.id), charge = reading(ctx, 'charge', unit.id);
    const onBattery = reading(ctx, 'onBattery', unit.id), online = reading(ctx, 'online', unit.id);
    const status = reading(ctx, 'collector', unit.id);
    return {
      ...unit, load,
      charge: charge != null && charge <= 100 ? charge : null,
      runtime: reading(ctx, 'runtimeMin', unit.id), inputVoltage: reading(ctx, 'input', unit.id),
      inputState: inputState(onBattery, online),
      collection: status === 1 ? 'Scrape observed' : status === 0 ? 'Scrape failed' : ctx.mode === 'recorded' ? 'Not captured' : 'Unknown',
    };
  });
}

export type Unit = ReturnType<typeof units>[number];
