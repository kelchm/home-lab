import type { DomainContext, DomainQuery } from '../contract';
import { records } from '../data';
import type { Series } from '../../model/semantics';
import manifest from './queries.json';

export const queries = manifest as DomainQuery[];
export const ref = (key: string) => `D_k8s_${key}`;
const KINDS = ['Deployments', 'StatefulSets', 'DaemonSets'];
// The existing Home checks use this source-freshness guard. It is not a resource-health threshold.
const SOURCE_FRESH_SECONDS = 90;
export const CNPG_STALE_SECONDS = 26 * 3600;
export const LONGHORN_STALE_SECONDS = 30 * 3600;
export type ResourceKind = 'namespace' | 'controller' | 'service' | 'database' | 'volume' | 'certificate' | 'flux';
export const resourceKey = (kind: ResourceKind, ...parts: string[]) => JSON.stringify([kind, ...parts]);
export function resourceIdentity(key: string | null): { kind: ResourceKind; parts: string[] } | null {
  try {
    const parsed: unknown = JSON.parse(key ?? 'null');
    if (!Array.isArray(parsed) || parsed.length < 2 || parsed.some((item) => typeof item !== 'string')) return null;
    const [kind, ...parts] = parsed;
    if (!['namespace', 'controller', 'service', 'database', 'volume', 'certificate', 'flux'].includes(kind)) return null;
    return { kind, parts };
  } catch { return null; }
}

/** A sample outside the selected window is never a current fact about this page. */
export function observed(ctx: DomainContext, series: Series | undefined): number | null {
  if (!series) return null;
  const point = series.points.at(-1);
  if (!point || point[0] == null || point[0] < ctx.timeRange.from.valueOf() || point[0] > ctx.timeRange.to.valueOf()) return null;
  return point[1] == null || !Number.isFinite(point[1]) ? null : point[1];
}
const list = (ctx: DomainContext, key: string) => records(ctx.series, ref(key));
export function at(ctx: DomainContext, key: string, labels: Record<string, string> = {}): number | null {
  const found = list(ctx, key).filter((row) => Object.entries(labels).every(([name, value]) => (row.labels[name] ?? '') === value));
  return found.length === 1 ? observed(ctx, found[0]) : null;
}
const nonnegative = (n: number | null): n is number => n != null && n >= 0;
const count = (n: number | null): n is number => nonnegative(n) && Number.isInteger(n);
const fresh = (n: number | null) => n != null && n >= 0 && n <= SOURCE_FRESH_SECONDS;
export const ksmObserved = (ctx: DomainContext) => at(ctx, 'ksmUp') === 1 && fresh(at(ctx, 'ksmAge'));
export const longhornObserved = (ctx: DomainContext) => at(ctx, 'longhornCoverage') === 100 && fresh(at(ctx, 'longhornAge'));

function identities(ctx: DomainContext, keys: string[], fields: string[], required: string[]): Map<string, Record<string, string>> {
  const found = new Map<string, Record<string, string>>();
  for (const key of keys) for (const row of list(ctx, key)) {
    if (!required.every((name) => row.labels[name])) continue;
    const labels = Object.fromEntries(fields.map((name) => [name, row.labels[name] ?? '']));
    found.set(JSON.stringify(fields.map((name) => labels[name])), labels);
  }
  return found;
}

export interface Controller {
  key: string; namespace: string; name: string; kind: string;
  desired: number | null; available: number | null;
  state: 'unknown' | 'below-desired' | 'meets-desired' | 'scaled-to-zero';
  evidence: string;
}
export function controllerGroups(ctx: DomainContext) {
  return KINDS.map((kind) => {
    const total = at(ctx, 'controllerTotal', { kind });
    const ready = at(ctx, 'controllerReady', { kind });
    const missing = at(ctx, 'controllerMissing', { kind });
    const observed = ksmObserved(ctx) && count(total) && count(ready) && count(missing) && ready + missing <= total;
    return { kind, total, ready, missing, observed };
  });
}
export function controllers(ctx: DomainContext): Controller[] {
  const names = identities(ctx, ['controllerInventory', 'controllerDeficit', 'controllerUnknown', 'controllerAvailable'], ['namespace', 'kind', 'controller'], ['namespace', 'kind', 'controller']);
  const groups = controllerGroups(ctx);
  return [...names.values()].map((labels): Controller => {
    const { namespace, kind, controller: name } = labels;
    const desired = at(ctx, 'controllerInventory', labels);
    const deficit = at(ctx, 'controllerDeficit', labels);
    const missing = list(ctx, 'controllerUnknown').some((row) => Object.entries(labels).every(([k, v]) => row.labels[k] === v));
    const group = groups.find((item) => item.kind === kind);
    const namedCount = [...names.values()].filter((item) => item.kind === kind).length;
    const availableRows = list(ctx, 'controllerAvailable').filter((row) => Object.entries(labels).every(([k, v]) => row.labels[k] === v));
    const ambiguous = availableRows.length > 1;
    let available = ksmObserved(ctx) && !missing && !ambiguous ? at(ctx, 'controllerAvailable', labels) : null;
    let evidence = 'Availability status unavailable';
    if (available != null) evidence = 'Named availability observation';
    else if (ksmObserved(ctx) && !missing && !ambiguous && count(desired) && nonnegative(deficit) && deficit <= desired) {
      available = desired - deficit; evidence = 'Named replica deficit';
    } else if (!missing && !ambiguous && group?.observed && group.total === namedCount && group.total === group.ready && group.missing === 0 && count(desired)) {
      available = desired; evidence = 'Complete named inventory and matching controller-kind counts';
    }
    const known = ksmObserved(ctx) && count(desired) && count(available);
    const state = !known ? 'unknown' : available! < desired! ? 'below-desired' : desired === 0 && available === 0 ? 'scaled-to-zero' : 'meets-desired';
    return { key: resourceKey('controller', namespace, kind, name), namespace, kind, name, desired, available: known ? available : null, state, evidence: !ksmObserved(ctx) ? 'kube-state-metrics is missing, failing or stale' : evidence };
  }).sort((a, b) => a.namespace.localeCompare(b.namespace) || a.name.localeCompare(b.name) || a.kind.localeCompare(b.kind));
}

export function namespaces(ctx: DomainContext) {
  const inventory = identities(ctx, ['nsPods', 'nsCpu', 'nsMem', 'nsRestarts', 'controllerInventory'], ['namespace'], ['namespace']);
  const workloads = controllers(ctx);
  return [...inventory.values()].map(({ namespace }) => ({
    key: resourceKey('namespace', namespace), name: namespace,
    running: ksmObserved(ctx) ? at(ctx, 'nsPods', { namespace }) : null,
    cpu: at(ctx, 'nsCpu', { namespace }), memory: at(ctx, 'nsMem', { namespace }),
    restarts: ksmObserved(ctx) ? at(ctx, 'nsRestarts', { namespace }) : null,
    controllers: workloads.filter((item) => item.namespace === namespace),
  })).sort((a, b) => (b.cpu ?? -1) - (a.cpu ?? -1) || a.name.localeCompare(b.name));
}

export function services(ctx: DomainContext) {
  return [...identities(ctx, ['routeRate', 'routeErrors', 'routeP95'], ['svc'], ['svc']).values()].map(({ svc }) => {
    const rate = at(ctx, 'routeRate', { svc });
    return {
      key: resourceKey('service', svc), name: svc, perMinute: nonnegative(rate) ? rate : null,
      errors: nonnegative(rate) && rate > 0 ? at(ctx, 'routeErrors', { svc }) : null,
      p95: nonnegative(rate) && rate > 0 ? at(ctx, 'routeP95', { svc }) : null,
    };
  }).sort((a, b) => (b.perMinute ?? -1) - (a.perMinute ?? -1) || a.name.localeCompare(b.name));
}

export interface Backup {
  state: 'unknown' | 'never' | 'stale' | 'recent' | 'exempt'; age: number | null; timestamp: number | null;
}
export function backupState(timestamp: number | null, now: number, staleAfter: number, observable = true): Backup {
  if (!observable || timestamp == null || !Number.isFinite(timestamp) || timestamp < 0 || timestamp > now / 1000) return { state: 'unknown', age: null, timestamp: null };
  if (timestamp === 0) return { state: 'never', age: null, timestamp: 0 };
  const age = now / 1000 - timestamp;
  return { state: age > staleAfter ? 'stale' : 'recent', age, timestamp };
}
export function databases(ctx: DomainContext) {
  return [...identities(ctx, ['dbExpected', 'dbUp', 'dbSize', 'dbBackends', 'dbBackupTimestamp', 'dbWal'], ['namespace', 'pod'], ['pod']).values()].map((labels) => {
    const { namespace, pod } = labels;
    // An old pod-only capture cannot be assigned to a namespaced pod with the same name.
    const up = at(ctx, 'dbUp', labels);
    const expected = at(ctx, 'dbExpected', labels);
    const endpointObserved = up === 1;
    const rawTimestamp = at(ctx, 'dbBackupTimestamp', labels);
    const backup = backupState(rawTimestamp, ctx.timeRange.to.valueOf(), CNPG_STALE_SECONDS, endpointObserved);
    return {
      key: resourceKey('database', namespace, pod), namespace: namespace || null, pod,
      expected: count(expected) && expected > 0, up,
      size: endpointObserved ? at(ctx, 'dbSize', labels) : null,
      backends: endpointObserved ? at(ctx, 'dbBackends', labels) : null,
      wal: endpointObserved ? at(ctx, 'dbWal', labels) : null, backup,
    };
  }).sort((a, b) => (a.namespace ?? '').localeCompare(b.namespace ?? '') || a.pod.localeCompare(b.pod));
}

export function longhornSummary(ctx: DomainContext) {
  const expected = at(ctx, 'backupExpected'); const recent = at(ctx, 'backupRecent');
  const stale = at(ctx, 'backupStale'); const never = at(ctx, 'backupNever'); const missing = at(ctx, 'backupMissing');
  const observed = longhornObserved(ctx) && count(expected) && expected > 0 && count(missing);
  const accounted = observed && count(recent) && count(stale) && count(never) && recent + stale + never + missing === expected;
  return { expected, recent, stale, never, missing, observed, accounted, target: at(ctx, 'backupTarget') };
}

export function volumes(ctx: DomainContext) {
  const names = identities(ctx, ['volumeInventory', 'volumeState', 'volumeBackupTimestamp', 'backupExceptions'], ['volume', 'pvc_namespace', 'pvc'], ['volume']);
  return [...names.values()].map((labels) => {
    const { volume, pvc_namespace: namespace, pvc } = labels;
    const matches = (row: Series) => Object.entries(labels).every(([k, v]) => (row.labels[k] ?? '') === v);
    const states = list(ctx, 'volumeState').filter((row) => matches(row) && observed(ctx, row) === 1).map((row) => row.labels.state);
    const problems = list(ctx, 'backupExceptions').filter(matches).map((row) => row.labels.problem);
    const exempt = list(ctx, 'volumeExempt').some((row) => row.labels.pvc_namespace === namespace && row.labels.pvc === pvc && namespace && pvc && observed(ctx, row) === 1);
    const backup = backupState(at(ctx, 'volumeBackupTimestamp', labels), ctx.timeRange.to.valueOf(), LONGHORN_STALE_SECONDS, longhornObserved(ctx));
    if (longhornObserved(ctx) && exempt) backup.state = 'exempt';
    // Captured exception lists preserve named problems even when the complete per-volume inventory was not captured.
    if (longhornObserved(ctx) && backup.state === 'unknown' && problems.length === 1 && (problems[0] === 'stale' || problems[0] === 'never')) backup.state = problems[0];
    return { key: resourceKey('volume', namespace, pvc, volume), namespace: namespace || null, pvc: pvc || null, volume,
      capacity: longhornObserved(ctx) ? at(ctx, 'volumeInventory', labels) : null,
      robustness: longhornObserved(ctx) && states.length === 1 ? states[0] : null, backup,
      issue: problems.includes('missing') ? 'Backup timestamp missing' : null,
    };
  }).sort((a, b) => (a.namespace ?? '').localeCompare(b.namespace ?? '') || (a.pvc ?? a.volume).localeCompare(b.pvc ?? b.volume));
}

export function certificates(ctx: DomainContext) {
  return [...identities(ctx, ['certLeft', 'certLife'], ['namespace', 'name'], ['name']).values()].map((labels) => ({
    key: resourceKey('certificate', labels.namespace, labels.name), namespace: labels.namespace || null, name: labels.name,
    seconds: at(ctx, 'certLeft', labels), lifetime: at(ctx, 'certLife', labels),
  })).sort((a, b) => (a.seconds ?? Infinity) - (b.seconds ?? Infinity) || a.name.localeCompare(b.name));
}

export function fluxResources(ctx: DomainContext) {
  return [...identities(ctx, ['fluxInventory'], ['namespace', 'kind', 'name'], ['namespace', 'kind', 'name']).values()].map((labels) => {
    const found = list(ctx, 'fluxInventory').filter((row) => Object.entries(labels).every(([k, v]) => row.labels[k] === v));
    const row = found.length === 1 && observed(ctx, found[0]) === 1 ? found[0] : null;
    return { namespace: labels.namespace, kind: labels.kind, name: labels.name, key: resourceKey('flux', labels.namespace, labels.kind, labels.name),
      ready: row?.labels.ready ?? null, suspended: row?.labels.suspended ?? null };
  }).sort((a, b) => a.namespace.localeCompare(b.namespace) || a.name.localeCompare(b.name));
}

export interface Attention {
  key: string; resource: string; finding: string; next: string; state: 'warning' | 'unknown' | 'neutral';
}
export function attention(ctx: DomainContext): Attention[] {
  const findings: Attention[] = [];
  for (const row of controllers(ctx)) if (row.state === 'below-desired') findings.push({ key: row.key, resource: `${row.namespace}/${row.name}`, finding: `${row.kind}: ${row.available} / ${row.desired} available replicas`, next: 'Inspect controller and pod status in this namespace.', state: 'warning' });
  for (const row of databases(ctx)) {
    if (row.up === 0) findings.push({ key: row.key, resource: [row.namespace, row.pod].filter(Boolean).join('/'), finding: 'Database collector reports unavailable', next: 'Check the pod and its metrics endpoint; backup state is unknown.', state: 'unknown' });
    if (row.backup.state === 'stale' || row.backup.state === 'never') findings.push({ key: row.key, resource: [row.namespace, row.pod].filter(Boolean).join('/'), finding: row.backup.state === 'never' ? 'No completed base backup recorded' : 'Base backup older than the existing 26h rule', next: 'Inspect Backup objects and plugin logs, then the store on Athena.', state: 'warning' });
    if (row.wal != null && row.wal > 0) findings.push({ key: row.key, resource: [row.namespace, row.pod].filter(Boolean).join('/'), finding: `${row.wal} WAL segments waiting to archive`, next: 'Inspect the Barman plugin and backing store; alert duration is not established here.', state: 'warning' });
  }
  for (const row of volumes(ctx)) if (['stale', 'never', 'unknown'].includes(row.backup.state) || ['degraded', 'faulted'].includes(row.robustness ?? '')) findings.push({ key: row.key, resource: [row.namespace, row.pvc ?? row.volume].filter(Boolean).join('/'), finding: ['degraded', 'faulted'].includes(row.robustness ?? '') ? `Longhorn volume ${row.robustness}` : `Volume backup ${row.backup.state}`, next: 'Inspect this claim in Longhorn Operations.', state: row.backup.state === 'unknown' && row.robustness !== 'degraded' && row.robustness !== 'faulted' ? 'unknown' : 'warning' });
  for (const row of certificates(ctx)) if (row.seconds != null && row.seconds <= 0) findings.push({ key: row.key, resource: row.name, finding: 'Certificate has reached its recorded expiry', next: 'Inspect the certificate and renewal controller.', state: 'warning' });
  for (const row of fluxResources(ctx)) if (row.ready !== 'True' && row.suspended !== 'True') findings.push({ key: row.key, resource: `${row.namespace}/${row.name}`, finding: row.ready == null ? 'Reconciliation status unknown' : `Flux ready=${row.ready}`, next: 'Inspect this resource in the Flux control plane.', state: row.ready == null ? 'unknown' : 'warning' });
  for (const row of list(ctx, 'alerts')) {
    if (row.labels.cluster !== 'k8s-prod' || observed(ctx, row) !== 1) continue;
    const ns = row.labels.namespace;
    findings.push({ key: resourceKey('namespace', ns ?? ''), resource: [ns, row.labels.pod ?? row.labels.service ?? row.labels.alertname].filter(Boolean).join('/'), finding: `${row.labels.alertname} · ${row.labels.severity ?? 'severity not supplied'}`, next: 'Inspect the reported alert and its source; correlation does not establish a cause.', state: row.labels.severity === 'critical' || row.labels.severity === 'warning' ? 'warning' : 'neutral' });
  }
  return findings;
}
