import type { DomainContext, DomainQuery } from '../contract';
import { records } from '../data';
import type { FrameLike, Series } from '../../model/semantics';
import manifest from './queries.json';

export const queries = manifest as DomainQuery[];
export const ref = (key: string) => `D_net_${key}`;
const captured = new Set(queries.filter((query) => query.capture).map((query) => query.refId));

export const PATHS = ['Starlink (WAN2)', 'T-Mobile (WAN1)'];

export type Collection = 'observed' | 'failed' | 'unknown';
export type LinkState = 'online' | 'offline' | 'unknown';
export interface NetworkPath {
  name: string;
  group: string | null;
  state: LinkState;
  age: number | null;
  latency: number | null;
  availability: number | null;
  provisionedDown: number | null;
  provisionedUp: number | null;
}
export interface NetworkDevice {
  name: string;
  type: string;
  version: string | null;
  identityObserved: boolean;
  metricsObserved: boolean;
  clients: number | null;
  cpu: number | null;
  memory: number | null;
  temperature: number | null;
  uptime: number | null;
}
export interface NetworkRadio {
  id: string;
  name: string | null;
  band: string | null;
  channel: number | null;
  busy: number | null;
  clients: number | null;
}
export interface NetworkPort {
  id: string;
  name: string | null;
  rx: number | null;
  tx: number | null;
}

/** Recorded mode must not promote accidentally supplied live-only frames into captured facts. */
export function availableFrames(ctx: DomainContext): FrameLike[] {
  return ctx.series.filter((frame) => ctx.mode === 'live' || captured.has(frame.refId ?? ''));
}

/** Read only the end of this window. A trailing null or absent cadence stays unknown. */
export function atEnd(series: Series | undefined, ctx: DomainContext): number | null {
  if (!series) return null;
  const from = ctx.timeRange.from.valueOf();
  const to = ctx.timeRange.to.valueOf();
  const last = series.points.filter(([time]) => time != null && time >= from && time <= to).sort(([a], [b]) => a - b).at(-1);
  const tolerance = Math.max(0, (series.step ?? 0) * 1.5);
  return last && to - last[0] <= tolerance && last[1] != null && Number.isFinite(last[1]) ? last[1] : null;
}

export function reading(ctx: DomainContext, key: string, labels: Record<string, string> = {}): number | null {
  const found = records(availableFrames(ctx), ref(key)).filter((item) => Object.entries(labels).every(([key, value]) => item.labels[key] === value));
  return found.length === 1 ? atEnd(found[0], ctx) : null;
}

export const collection = (ctx: DomainContext, key: string): Collection => {
  const value = reading(ctx, `${key}Collector`);
  return value === 1 ? 'observed' : value === 0 ? 'failed' : 'unknown';
};

function names(ctx: DomainContext, keys: string[], label: string): string[] {
  return [...new Set(keys.flatMap((key) => records(availableFrames(ctx), ref(key)).map((item) => item.labels[label]).filter(Boolean)))];
}

export function paths(ctx: DomainContext): NetworkPath[] {
  const identities = [...new Set([...PATHS, ...names(ctx, ['wanState', 'latency', 'availability'], 'wan'), ...names(ctx, ['wanMap'], 'wan_interface')])].sort();
  return identities.map((name) => {
    const state = reading(ctx, 'wanState', { wan: name });
    const age = reading(ctx, 'wanAge', { wan: name });
    const maps = records(availableFrames(ctx), ref('wanMap')).filter((item) => item.labels.wan_interface === name);
    // Same 180-second metric-freshness guard used in the accepted Home WAN check, not a path-health threshold.
    const observed = collection(ctx, 'unifi') !== 'failed' && age != null && age >= 0 && age <= 180;
    return {
      name, group: maps.length === 1 ? maps[0].labels.wan_networkgroup ?? null : null,
      state: observed && state === 1 ? 'online' : observed && state === 0 ? 'offline' : 'unknown', age,
      latency: reading(ctx, 'latency', { wan: name }), availability: reading(ctx, 'availability', { wan: name }),
      provisionedDown: reading(ctx, 'linkDown', { wan: name }), provisionedUp: reading(ctx, 'linkUp', { wan: name }),
    };
  });
}

const deviceKeys = ['deviceInfo', 'deviceClients', 'deviceCpu', 'deviceMem', 'deviceTemp', 'deviceUptime'];
export function devices(ctx: DomainContext): NetworkDevice[] {
  const radioNames = new Set(radios(ctx).flatMap((radio) => radio.name ? [radio.name] : []));
  const identities = [...new Set([...names(ctx, deviceKeys, 'name'), ...radioNames])].sort();
  return identities.map((name) => {
    const info = records(availableFrames(ctx), ref('deviceInfo')).filter((item) => item.labels.name === name && atEnd(item, ctx) === 1);
    const one = (key: string) => reading(ctx, key, { name });
    const [clients, cpu, memory, temperature, uptime] = ['deviceClients', 'deviceCpu', 'deviceMem', 'deviceTemp', 'deviceUptime'].map(one);
    return {
      name, type: info.length === 1 ? info[0].labels.type ?? 'Unknown' : radioNames.has(name) ? 'radio' : 'Unknown',
      version: info.length === 1 ? info[0].labels.version ?? null : null, identityObserved: info.length === 1,
      metricsObserved: [clients, cpu, memory, temperature, uptime].some((value) => value != null),
      clients, cpu, memory, temperature, uptime,
    };
  });
}

/** Old captures retain only ap_radio; split its final band suffix without truncating dotted device names. */
export function radioIdentity(labels: Record<string, string>): { id: string; name: string | null; band: string | null } | null {
  if (labels.ap_radio) {
    const match = /^(.*) · ([0-9.]+) GHz$/.exec(labels.ap_radio);
    return { id: labels.ap_radio, name: labels.name ?? match?.[1] ?? null, band: labels.band ?? match?.[2] ?? null };
  }
  if (!labels.name || !(labels.band || labels.radio)) return null;
  const band = labels.band ?? ({ ng: '2.4', na: '5', '6e': '6' }[labels.radio] ?? null);
  return { id: `${labels.name} · ${band ? `${band} GHz` : labels.radio}`, name: labels.name, band };
}

export function radios(ctx: DomainContext): NetworkRadio[] {
  const keys = ['radioChannel', 'radioBusy', 'radioClients'];
  const series = keys.map((key) => records(availableFrames(ctx), ref(key)));
  const all = series.flatMap((items) => items.flatMap((item) => { const identity = radioIdentity(item.labels); return identity ? [identity] : []; }));
  return [...new Map(all.map((identity) => [identity.id, identity])).values()].sort((a, b) => a.id.localeCompare(b.id)).map((identity) => {
    const values = series.map((items) => {
      const matches = items.filter((item) => radioIdentity(item.labels)?.id === identity.id);
      return matches.length === 1 ? atEnd(matches[0], ctx) : null;
    });
    return { ...identity, channel: values[0], busy: values[1], clients: values[2] };
  });
}

export function ports(ctx: DomainContext): NetworkPort[] {
  const keys = ['wanRxHistory', 'wanTxHistory'];
  // Include status identities even when one counter disappears; never infer a provider from ordering or eth8/eth9.
  const ids = names(ctx, [...keys, 'wanPorts'], 'port');
  return ids.sort().map((id) => ({ id, name: null, rx: reading(ctx, keys[0], { port: id }), tx: reading(ctx, keys[1], { port: id }) }));
}

/** Native switch port identifiers are scoped by device, not globally unique port numbers. */
export function devicePorts(ctx: DomainContext, name: string): NetworkPort[] {
  const list = ['portRxHistory', 'portTxHistory'].map((key) => records(availableFrames(ctx), ref(key)).filter((item) => item.labels.name === name));
  const identity = (item: Series) => item.labels.port_id || item.labels.port || item.labels.port_num;
  const ids = [...new Set(list.flatMap((items) => items.map(identity).filter(Boolean)))].sort();
  return ids.map((id) => {
    const pairs = list.map((items) => items.filter((item) => identity(item) === id));
    const labels = pairs.flat().map((item) => item.labels.port_name).filter(Boolean);
    return { id, name: new Set(labels).size === 1 ? labels[0] : null, rx: pairs[0].length === 1 ? atEnd(pairs[0][0], ctx) : null, tx: pairs[1].length === 1 ? atEnd(pairs[1][0], ctx) : null };
  });
}

/** The captured `or vector(0)` registration query cannot distinguish missing registration from an explicit negative. */
export function registration(ctx: DomainContext): string {
  const result = reading(ctx, 'tmobileRegistered');
  if (collection(ctx, 'tmobile') === 'failed') return 'Unknown · collector failed';
  return result === 1 ? 'Registered observation' : 'Unknown · no positive registration observation';
}
