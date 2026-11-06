import type { DomainContext, DomainQuery } from '../contract';
import { records } from '../data';
import type { Series } from '../../model/semantics';
import manifest from './queries.json';

export const queries = manifest as DomainQuery[];
export const ref = (key: string) => `D_dev_${key}`;
export const PIS = [
  { id: 'adsb-office', role: 'ADS-B · office', timeServer: false },
  { id: 'streamer-basement-workbench', role: 'Streamer · basement workbench', timeServer: false },
  { id: 'ups-compute-rack', role: 'UPS collector · compute rack', timeServer: true },
  { id: 'ups-office', role: 'UPS collector · office', timeServer: true },
] as const;

export function current(ctx: DomainContext, series: Series | undefined): number | null {
  if (!series) return null;
  const from = ctx.timeRange.from.valueOf(), to = ctx.timeRange.to.valueOf();
  const point = series.points.filter(([at]) => at >= from && at <= to).sort((a, b) => a[0] - b[0]).at(-1);
  return point && to - point[0] <= Math.max(60000, (series.step ?? 0) * 1.5) && point[1] != null && Number.isFinite(point[1]) ? point[1] : null;
}

function observations(ctx: DomainContext, key: string): Series[] {
  if (ctx.mode === 'recorded' && !queries.find((query) => query.key === key)?.capture) return [];
  return records(ctx.series, ref(key));
}

export function reading(ctx: DomainContext, key: string, labels: Record<string, string> = {}): number | null {
  const list = observations(ctx, key).filter((series) => Object.entries(labels).every(([name, value]) => series.labels[name] === value));
  return list.length === 1 ? current(ctx, list[0]) : null;
}

const nonnegative = (v: number | null) => v != null && v >= 0 ? v : null;
const percent = (v: number | null) => v != null && v >= 0 && v <= 100 ? v : null;
export function collection(value: number | null): 'Scrape observed' | 'Scrape failed' | 'Unknown' {
  return value === 1 ? 'Scrape observed' : value === 0 ? 'Scrape failed' : 'Unknown';
}

export function hosts(ctx: DomainContext) {
  const identities = observations(ctx, 'inventory').filter((series) => series.labels.platform === 'pi').map((series) => series.labels.instance).filter(Boolean);
  const names = [...new Set([...PIS.map((host) => host.id), ...identities])].sort();
  return names.map((id) => {
    const known = PIS.find((host) => host.id === id);
    const labels = { instance: id, platform: 'pi' };
    return {
      id, role: known?.role ?? 'Supporting Pi · observed identity', timeServer: known?.timeServer ?? false,
      collection: collection(reading(ctx, 'hostUp', labels)),
      cpu: percent(reading(ctx, 'cpu', labels)), memory: percent(reading(ctx, 'memory', labels)),
      temperature: reading(ctx, 'piTemp', { instance: id }), age: nonnegative(reading(ctx, 'scrapeAge', labels)),
    };
  });
}

export function recovery(ctx: DomainContext) {
  const up = reading(ctx, 'kvmUp');
  return {
    id: 'glkvm', collection: collection(ctx.mode === 'recorded' && up === 0 ? null : up),
    cpu: percent(reading(ctx, 'kvmCpu')), memory: percent(reading(ctx, 'kvmMem')), temperature: reading(ctx, 'kvmTemp'),
  };
}

export type SyncState = 'Unknown' | 'Collection failed' | 'Partial collection' | 'No recent scrape' | 'Unsynchronized' | 'Holdover' | 'Tracking synchronized';
export function timeService(ctx: DomainContext, instance: string) {
  const labels = { instance };
  const up = reading(ctx, 'ntpUp', labels), chrony = reading(ctx, 'chronyUp', labels);
  const age = nonnegative(reading(ctx, 'ntpScrapeAge', labels));
  const stratum = nonnegative(reading(ctx, 'stratum', labels));
  const referenceAge = nonnegative(reading(ctx, 'referenceAge', labels));
  let state: SyncState = 'Unknown';
  if (age != null && age >= 300) state = 'No recent scrape';
  else if (up === 0) state = 'Collection failed';
  else if (up === 1 && chrony === 0) state = 'Partial collection';
  else if (up === 1 && chrony === 1 && age != null && stratum != null) {
    if (stratum === 0 || stratum >= 16) state = 'Unsynchronized';
    else if (Number.isInteger(stratum) && referenceAge != null) state = referenceAge > 3600 ? 'Holdover' : 'Tracking synchronized';
  }
  const tracking = observations(ctx, 'tracking').filter((series) => series.labels.instance === instance && current(ctx, series) === 1);
  const source = tracking.length === 1 ? tracking[0].labels.tracking_address || null : null;
  return { instance, state, stratum, referenceAge, source, age };
}

export function timeServices(ctx: DomainContext) {
  const observed = observations(ctx, 'ntpUp').map((series) => series.labels.instance).filter(Boolean);
  return [...new Set([...PIS.filter((host) => host.timeServer).map((host) => host.id), ...observed])].sort().map((id) => timeService(ctx, id));
}

export function timeSources(ctx: DomainContext) {
  const keys = ['sourceState', 'sourceReach', 'sourceAge'];
  const identities = new Map<string, { instance: string; address: string }>();
  for (const key of keys) for (const series of observations(ctx, key)) {
    const instance = series.labels.instance, address = series.labels.source_address;
    if (instance && address) identities.set(JSON.stringify([instance, address]), { instance, address });
  }
  return [...identities.entries()].map(([id, { instance, address }]) => {
    const labels = { instance, source_address: address };
    const states = observations(ctx, 'sourceState').filter((series) => series.labels.instance === instance && series.labels.source_address === address && current(ctx, series) === 1);
    const reach = reading(ctx, 'sourceReach', labels);
    return { id, instance, address, state: states.length === 1 ? states[0].labels.source_state || 'Unknown' : 'Unknown',
      reach: reach != null && reach >= 0 && reach <= 1 ? reach * 8 : null, age: nonnegative(reading(ctx, 'sourceAge', labels)) };
  });
}
