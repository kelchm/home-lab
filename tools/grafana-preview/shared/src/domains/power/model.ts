import type { DomainContext, DomainQuery } from '../contract';
import { current, records } from '../data';
import manifest from './queries.json';

export const queries = manifest as DomainQuery[];
export const ref = (key: string) => `D_power_${key}`;
export const UNITS = [
  { id: 'compute-rack', name: 'Compute rack', collector: 'ups-compute-rack', source: 'NUT · supporting Pi' },
  { id: 'network-rack', name: 'Network rack', collector: 'nas', source: 'Synology SNMP · Athena' },
  { id: 'office', name: 'Office', collector: 'ups-office', source: 'NUT · supporting Pi' },
] as const;

/** Requires a unique window-end query result; its evaluation timestamp does not establish source sample freshness. */
export function reading(ctx: DomainContext, key: string, ups: string): number | null {
  const query = queries.find((item) => item.key === key);
  if (ctx.mode === 'recorded' && !query?.capture) return null;
  const list = records(ctx.series, ref(key)).filter((item) => item.labels.ups === ups);
  if (list.length !== 1) return null;
  const value = current(ctx, list[0]);
  return value != null && value >= 0 ? value : null;
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
