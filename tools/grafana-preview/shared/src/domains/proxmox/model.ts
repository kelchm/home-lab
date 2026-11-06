import type { DomainContext, DomainQuery } from '../contract';
import { labelValue, records } from '../data';
import type { Series } from '../../model/semantics';
import definitions from './queries.json';

export const queries: DomainQuery[] = definitions as DomainQuery[];
export const ref = (key: string) => `D_pve_${key}`;
// Repository policy inventory, not an assertion that the nodes are currently available.
export const EXPECTED_NODES = ['pve-sbx-1', 'pve-sbx-2', 'pve-sbx-3'];
export const DAILY_JOB = 'daily-backups';
const HOURS = 3600;

export function observations(ctx: DomainContext, key: string): Series[] {
  return records(ctx.series, ref(key)).filter((s) => (!s.labels.cluster || s.labels.cluster === 'pve-sbx') && (!s.labels.platform || s.labels.platform === 'pve'));
}
function sample(ctx: DomainContext, series: Series | undefined): number | null {
  if (!series) return null;
  const points = series.points.filter(([at]) => at >= ctx.timeRange.from.valueOf() && at <= ctx.timeRange.to.valueOf());
  const value = labelValue({ ...series, points });
  return value != null && Number.isFinite(value) ? value : null;
}
export function scalar(ctx: DomainContext, key: string): number | null {
  const found = observations(ctx, key);
  return found.length === 1 ? sample(ctx, found[0]) : null;
}
const one = (ctx: DomainContext, found: Series[]) => found.length === 1 ? sample(ctx, found[0]) : null;
export type Membership = 'included' | 'excluded' | 'unknown';
export interface Guest {
  key: string;
  entity: string;
  id: string | null;
  name: string;
  node: string | null;
  type: string | null;
  tags: string | null;
  up: number | null;
  onboot: number | null;
  cpu: number | null;
  memory: number | null;
  memoryBytes: number | null;
  memorySize: number | null;
  diskSize: number | null;
  uptime: number | null;
  membership: Membership;
}

export function guests(ctx: DomainContext): Guest[] {
  const inventory = observations(ctx, 'guestInfo').filter((s) => sample(ctx, s) === 1 && Boolean(s.labels.name || s.labels.id));
  const counts = new Map<string, number>();
  for (const s of inventory) counts.set(s.labels.name, (counts.get(s.labels.name) ?? 0) + 1);
  return inventory.map((s): Guest => {
    const labels = s.labels;
    const measure = (key: string) => one(ctx, observations(ctx, key).filter((r) => {
      // Full identity wins. Name-only recordings may attach only to a unique inventory name.
      if (r.labels.id) return Boolean(labels.id) && r.labels.id === labels.id && (!r.labels.node || r.labels.node === labels.node);
      return Boolean(labels.name) && r.labels.name === labels.name && counts.get(labels.name) === 1 && (!r.labels.node || r.labels.node === labels.node);
    }));
    const omitted = measure('guestNotBackedUp');
    const membership: Membership = omitted === 1 ? 'excluded' : omitted === 0 || (ctx.mode === 'live' && scalar(ctx, 'backupInventory') === 0) ? 'included' : 'unknown';
    return {
      key: labels.id ?? `${labels.node ?? '?'}:${labels.type ?? '?'}:${labels.name}`,
      entity: labels.id ?? labels.name, id: labels.id ?? null,
      name: labels.name || labels.id, node: labels.node ?? null, type: labels.type ?? null, tags: labels.tags ?? null,
      up: measure('guestUp'), onboot: measure('guestOnboot'), cpu: measure('guestCpu'), memory: measure('guestMemory'),
      memoryBytes: measure('guestMemoryBytes'), memorySize: measure('guestMemorySize'), diskSize: measure('guestDiskSize'), uptime: measure('guestUptime'), membership,
    };
  }).sort((a, b) => a.name.localeCompare(b.name));
}

export interface Host { name: string; collection: number | null; apiUp: number | null; cpu: number | null; memory: number | null; age: number | null }
export function hosts(ctx: DomainContext): Host[] {
  const names = new Set(EXPECTED_NODES);
  for (const key of ['hostInfo', 'hostUp', 'hostCpu', 'hostMemory']) for (const s of observations(ctx, key)) {
    if (s.labels.platform === 'pve' && s.labels.instance) names.add(s.labels.instance);
  }
  for (const guest of guests(ctx)) if (guest.node) names.add(guest.node);
  for (const s of observations(ctx, 'backupCompletion')) if (s.labels.instance) names.add(s.labels.instance);
  for (const s of observations(ctx, 'nodeUp')) if (s.labels.id?.startsWith('node/')) names.add(s.labels.id.slice(5));
  return [...names].sort().map((name) => {
    const measure = (key: string) => one(ctx, observations(ctx, key).filter((s) => s.labels.platform === 'pve' && s.labels.instance === name));
    const collection = measure('hostUp');
    return { name, collection, apiUp: one(ctx, observations(ctx, 'nodeUp').filter((s) => s.labels.id === `node/${name}`)), cpu: collection === 0 ? null : measure('hostCpu'), memory: collection === 0 ? null : measure('hostMemory'), age: measure('hostAge') };
  });
}

export interface Pool { key: string; node: string; storage: string; used: number | null }
export function pools(ctx: DomainContext): Pool[] {
  const grouped = new Map<string, Series[]>();
  for (const s of observations(ctx, 'pools')) {
    if (!s.labels.node || !s.labels.storage) continue;
    const key = `${s.labels.node}:${s.labels.storage}`;
    grouped.set(key, [...(grouped.get(key) ?? []), s]);
  }
  return [...grouped].map(([key, series]) => ({ key, node: series[0].labels.node, storage: series[0].labels.storage, used: one(ctx, series) }));
}

export interface Backup {
  key: string;
  node: string;
  job: string;
  success: number | null;
  failure: number | null;
  outcome: 'unknown' | 'clean' | 'failed' | 'retried';
  dailyFreshness: 'unknown' | 'within-policy' | 'outside-policy' | 'not-daily';
  failureVisibility: boolean;
}
export function backups(ctx: DomainContext): Backup[] {
  const all = observations(ctx, 'backupCompletion');
  const keys = new Map(EXPECTED_NODES.map((node) => [`${node}:${DAILY_JOB}`, { node, job: DAILY_JOB }]));
  for (const s of all) if (s.labels.instance && s.labels.backup_job) keys.set(`${s.labels.instance}:${s.labels.backup_job}`, { node: s.labels.instance, job: s.labels.backup_job });
  const end = ctx.timeRange.to.valueOf() / 1000;
  return [...keys].sort(([a], [b]) => a.localeCompare(b)).map(([key, { node, job }]) => {
    const timestamp = (outcome: string) => {
      const result = one(ctx, all.filter((s) => s.labels.instance === node && s.labels.backup_job === job && s.labels.outcome === outcome));
      // The metric value is a completion timestamp, not a status bit. Reject synthetic zero and future/expired values.
      return result != null && result > 0 && result <= end && end - result <= 48 * HOURS ? result : null;
    };
    const success = timestamp('info');
    const failure = timestamp('error');
    const outcome = failure != null && (success == null || failure >= success) ? 'failed' : success != null ? failure != null ? 'retried' : 'clean' : 'unknown';
    return {
      key, node, job, success, failure, outcome,
      dailyFreshness: job !== DAILY_JOB ? 'not-daily' : success == null ? 'unknown' : end - success < 30 * HOURS ? 'within-policy' : 'outside-policy',
      // Mirrors the documented rule's minimum visibility period without claiming Alertmanager is firing.
      failureVisibility: failure != null && (end - failure < HOURS || success == null || success <= failure),
    };
  });
}
