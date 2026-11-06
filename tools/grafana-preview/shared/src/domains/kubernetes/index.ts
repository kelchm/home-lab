import type { DomainCell, DomainContext, DomainDefinition, DomainRow, DomainSection } from '../contract';
import { format } from '../data';
import {
  at, attention, certificates, controllerGroups, controllers, databases, fluxResources, ksmObserved,
  longhornObserved, longhornSummary, namespaces, queries, ref, resourceIdentity, services, volumes,
  type Backup,
} from './model';

const f = (value: number | null, suffix = '', digits = 1) => format(value, digits, suffix);
// Existing namespace drilldown used by home-dashboard.json.
const NAMESPACE_DASHBOARD = 'a87fb0d919ec0ea5f6543124e16c42a5';
const n = (value: number | null) => f(value, '', 0);
const scaled = (value: number | null, factor: number) => value == null ? null : value / factor;
const detailHref = (ctx: DomainContext, entity: string) => ctx.href({ focus: 'Kubernetes', view: 'resource', entity });
const linked = (ctx: DomainContext, name: string, key: string): DomainCell => ({ text: name, href: detailHref(ctx, key) });
// Traefik's native dashboard filters the original exported_service, before Home's route normalization.
const nativeServiceFilter = (name: string) => {
  const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return `(${escaped}(@.*)?|httproute-${escaped}-gw-network-gateway.*)`;
};
const status = (text: string, tone: DomainCell['tone'] = 'neutral', detail?: string): DomainCell => ({ text, tone, detail });
const chart = (id: string, title: string, keys: string[], unit: string, description: string): DomainSection => ({ kind: 'chart', id, title, refs: keys.map(ref), unit, height: 235, description });
const table = (id: string, title: string, columns: string[], rows: DomainRow[], empty: string, description?: string): DomainSection => ({ kind: 'table', id, title, columns, rows, empty, description });
const nav = (ctx: DomainContext): DomainSection => ({ kind: 'links', id: 'k8s-destinations', title: 'Follow the question', items: [
  { label: 'Workloads and services', href: ctx.href({ focus: 'Kubernetes', view: 'workloads', entity: null }), description: 'Namespaces, named controllers, routes and reconciliation.' },
  { label: 'Storage and protection', href: ctx.href({ focus: 'Kubernetes', view: 'storage', entity: null }), description: 'Volume backups and database recovery are separate evidence paths.' },
  { label: 'Kubernetes / Views / Global', href: ctx.deeper('k8s_views_global'), description: 'Native cluster, namespace and pod investigation.' },
] });

function collection(ctx: DomainContext): DomainSection {
  return table('k8s-collection', 'Collection behind these observations', ['Source', 'Observed state', 'Source sample age', 'Meaning'], [
    { id: 'ksm', cells: ['kube-state-metrics', status(ksmObserved(ctx) ? 'Collecting' : 'Unknown / incomplete', ksmObserved(ctx) ? 'neutral' : 'unknown'), f(at(ctx, 'ksmAge'), ' s'), 'Inventory and controller availability require the existing 90s source freshness guard.'] },
    { id: 'longhorn', cells: ['Longhorn managers', status(longhornObserved(ctx) ? 'All expected managers collecting' : 'Unknown / incomplete', longhornObserved(ctx) ? 'neutral' : 'unknown'), f(at(ctx, 'longhornAge'), ' s'), `${f(at(ctx, 'longhornCoverage'), '%')} of expected manager scrapes; this does not establish database protection.`] },
    { id: 'cnpg', cells: ['Barman / CNPG', status(databases(ctx).some((row) => row.expected) ? 'Expected-pod inventory observed' : 'Expected-pod inventory unavailable', databases(ctx).some((row) => row.expected) ? 'neutral' : 'unknown'), 'Per pod below', 'A database collector answering does not establish a completed backup.'] },
  ], 'Collection has not been observed.');
}

function controllerTable(ctx: DomainContext, rows = controllers(ctx)): DomainSection {
  const label = { unknown: 'Availability unknown', 'below-desired': 'Below desired', 'meets-desired': 'Meets desired replicas', 'scaled-to-zero': 'Configured with zero replicas' };
  return table('k8s-controllers', 'Named controllers', ['Controller', 'Namespace', 'Kind', 'Desired / available', 'Observation'], rows.map((row) => ({ id: row.key, cells: [
    linked(ctx, row.name, row.key), row.namespace, row.kind, `${n(row.desired)} / ${n(row.available)}`,
    status(label[row.state], row.state === 'below-desired' ? 'warning' : row.state === 'unknown' ? 'unknown' : 'neutral', row.evidence),
  ] })), 'No named controller inventory is available. A zero aggregate cannot identify an absent controller.', 'Desired replica counts describe configuration. Meeting them does not prove an end-to-end service check.');
}

function namespaceTable(ctx: DomainContext, rows = namespaces(ctx)): DomainSection {
  return table('k8s-namespaces', 'Namespaces and workload context', ['Namespace', 'Running pods', 'CPU · 5m', 'Working memory', 'Restarts · 24h', 'Controllers needing inspection'], rows.map((row) => ({ id: row.key, cells: [
    linked(ctx, row.name, row.key), n(row.running), f(row.cpu, ' cores', 2), f(scaled(row.memory, 1024 ** 3), ' GiB', 2), n(row.restarts),
    row.controllers.length ? `${row.controllers.filter((item) => item.state === 'below-desired').length} below desired · ${row.controllers.filter((item) => item.state === 'unknown').length} unknown` : status('Controller inventory unavailable', 'unknown'),
  ] })), 'No namespace observations. Missing namespaces are not empty namespaces.', 'CPU and working memory are measurements, not requests. A missing restart series stays unknown; running pod count is not a readiness assertion.');
}

function serviceTable(ctx: DomainContext, rows = services(ctx)): DomainSection {
  return table('k8s-services', 'Traffic by observed route / service', ['Route / service', 'Requests / min · 1h', '5xx share · 1h', 'p95 · 1h', 'Next'], rows.map((row) => ({ id: row.key, cells: [
    linked(ctx, row.name, row.key), f(row.perMinute, '', 2),
    row.perMinute === 0 ? status('No request observations', 'unknown') : status(f(row.errors, '%', 2), row.errors == null ? 'unknown' : row.errors > 0 ? 'warning' : 'neutral'),
    row.perMinute === 0 ? 'No latency observation' : f(row.p95, ' s', 3),
    { text: 'Inspect route', href: detailHref(ctx, row.key), detail: 'Route identifiers are exporter labels; no namespace is guessed from a prefix.' },
  ] })), 'No route observations. This is not evidence that services are idle.', 'No error-family capture is available for replay. An observed traffic series cannot fill a missing error percentage with zero.');
}

function backupCell(backup: Backup, db = false): DomainCell {
  if (backup.state === 'never') return status('No completed backup recorded', 'warning', 'The timestamp was explicitly 0. Check Backup objects before concluding the backup itself failed.');
  if (backup.state === 'unknown') return status('Unknown', 'unknown', 'A missing or invalid timestamp, inventory gap or failing collector cannot establish a recovery point.');
  if (backup.state === 'exempt') return status('Exempt from volume backup policy', 'neutral', 'An exemption is not recovery evidence.');
  return status(`${f(scaled(backup.age, 3600), ' h')} old`, backup.state === 'stale' ? 'warning' : 'neutral', backup.state === 'stale' ? `Older than the existing ${db ? '26h CNPG' : '30h Longhorn'} rule; alert duration is not established.` : 'Recorded timestamp only; restore success is not established.');
}

function databaseTable(ctx: DomainContext, rows = databases(ctx)): DomainSection {
  return table('k8s-databases', 'CNPG instances and recovery evidence', ['Instance', 'Namespace', 'Expected inventory', 'Collector', 'Base backup', 'WAL waiting', 'Database size / backends'], rows.map((row) => ({ id: row.key, cells: [
    linked(ctx, row.pod, row.key), row.namespace ?? 'Not captured', row.expected ? 'Expected pod observed' : status('Unavailable', 'unknown'),
    status(row.up === 1 ? 'Answering' : row.up === 0 ? 'Reports unavailable' : 'Unknown', row.up === 1 ? 'neutral' : 'unknown'),
    backupCell(row.backup, true), status(n(row.wal), row.wal == null ? 'unknown' : row.wal > 0 ? 'warning' : 'neutral'), `${f(scaled(row.size, 1024 ** 3), ' GiB', 2)} / ${n(row.backends)}`,
  ] })), 'No expected-pod or CNPG instance inventory is available. Do not interpret this as no databases.', 'Barman timestamps are separate from Longhorn snapshots. Pod-only historical rows retain unknown namespaces; replica sizes are not summed as unique database bytes.');
}

function volumeTable(ctx: DomainContext, rows = volumes(ctx)): DomainSection {
  return table('k8s-volumes', 'Named volumes and backup exceptions', ['Claim / volume', 'Namespace', 'Robustness', 'Capacity', 'Volume backup'], rows.map((row) => ({ id: row.key, cells: [
    linked(ctx, row.pvc ?? row.volume, row.key), row.namespace ?? 'Not captured', status(row.robustness ?? 'Unknown', row.robustness == null ? 'unknown' : ['degraded', 'faulted'].includes(row.robustness) ? 'warning' : 'neutral'),
    f(scaled(row.capacity, 1024 ** 3), ' GiB'), row.issue ? status(row.issue, 'unknown') : backupCell(row.backup),
  ] })), 'Named volume inventory was not captured. Use native Longhorn Operations for claim detail; aggregate backup counts do not supply volume identities.', 'Robustness and backup freshness answer different questions. Unknown robustness is never replaced by the aggregate healthy count.');
}

function certificateTable(ctx: DomainContext, rows = certificates(ctx)): DomainSection {
  return table('k8s-certificates', 'Certificate expiry context', ['Certificate', 'Namespace', 'Time to expiry', 'Lifetime remaining'], rows.map((row) => ({ id: row.key, cells: [
    linked(ctx, row.name, row.key), row.namespace ?? 'Not captured', status(f(scaled(row.seconds, 86400), ' d', 2), row.seconds == null ? 'unknown' : row.seconds <= 0 ? 'warning' : 'neutral'), f(row.lifetime, '%'),
  ] })), 'No certificate observations.', 'Ordered by remaining time. No new renewal threshold is inferred; positive remaining lifetime does not establish renewal readiness.');
}

function fluxTable(ctx: DomainContext, rows = fluxResources(ctx)): DomainSection {
  return table('k8s-flux', 'Named Flux reconciliation', ['Resource', 'Namespace / kind', 'Ready report', 'Suspended'], rows.map((row) => ({ id: row.key, cells: [
    linked(ctx, row.name, row.key), `${row.namespace} / ${row.kind}`, status(row.ready ?? 'Unknown', row.suspended === 'True' ? 'neutral' : row.ready === 'True' ? 'neutral' : row.ready == null ? 'unknown' : 'warning'), row.suspended ?? 'Unknown',
  ] })), 'Named Flux inventory was not captured. The reported zero failure total cannot establish that every resource is reconciled.', 'A suspended resource is configured to pause reconciliation; investigate it in context.');
}

function protectionSummary(ctx: DomainContext): DomainSection {
  const summary = longhornSummary(ctx);
  const dbs = databases(ctx);
  const dbEvidence = dbs.length > 0 && dbs.every((row) => row.expected && row.backup.state !== 'unknown' && row.wal != null && row.up === 1);
  return table('k8s-protection-paths', 'Keep the recovery paths separate', ['Path', 'Observation', 'Evidence limits', 'Next'], [
    { id: 'lh', cells: ['Longhorn volume backups', status(summary.accounted ? `${n(summary.recent)} / ${n(summary.expected)} within 30h` : 'Coverage / accounting incomplete', summary.accounted ? 'neutral' : 'unknown'), `${n(summary.stale)} stale · ${n(summary.never)} never recorded · ${n(summary.missing)} missing timestamps. Counts exclude policy exemptions.`, { text: 'Inspect volume protection', href: ctx.deeper('longhorn-operations') }] },
    { id: 'target', cells: ['Longhorn backup target', status(summary.target === 1 && longhornObserved(ctx) ? 'Reports available' : summary.target === 0 && longhornObserved(ctx) ? 'Reports unavailable' : 'Unknown', summary.target === 0 && longhornObserved(ctx) ? 'warning' : summary.target === 1 && longhornObserved(ctx) ? 'neutral' : 'unknown'), 'Target availability does not prove backup completion or a restore.', { text: 'Longhorn Operations', href: ctx.deeper('longhorn-operations') }] },
    { id: 'db', cells: ['CNPG base backup + WAL', status(dbEvidence ? 'Inspect per-instance evidence' : 'Recovery evidence incomplete', dbEvidence ? 'neutral' : 'unknown'), ctx.mode === 'recorded' ? 'The empty legacy collector backup capture is excluded. Current Barman timestamps and expected-pod inventory were not captured.' : 'Expected pods, completed base-backup timestamps and WAL archiving each require their own observations.', { text: 'Inspect database protection', href: ctx.href({ focus: 'Kubernetes', view: 'storage', entity: null }) }] },
    { id: 'restore', cells: ['Restore verification', status('Not established', 'unknown'), 'Neither a recent volume backup nor a database timestamp demonstrates a usable restore.', { text: 'Storage on Athena', href: ctx.href({ focus: 'Athena', entity: null }) }] },
  ], 'No protection evidence.');
}

function overview(ctx: DomainContext): DomainSection[] {
  const groups = controllerGroups(ctx);
  const issues = attention(ctx);
  return [
    table('k8s-attention', 'Resources to inspect', ['Resource', 'Observation', 'Next action'], issues.map((row, index) => ({ id: `${row.key}:${index}`, cells: [linked(ctx, row.resource, row.key), status(row.finding, row.state), row.next] })), 'No named exception observations in the available data. Review collection and protection gaps before concluding that no action is needed.', 'Observations belong to the selected window. Informational alerts retain their reported severity and do not establish a diagnosis.'),
    table('k8s-control-context', 'Controller inventory and cluster context', ['Scope', 'Observation', 'Meaning'], [
      ...groups.map((group) => ({ id: group.kind, cells: [group.kind, status(group.observed ? `${n(group.ready)} / ${n(group.total)} meeting desired replicas` : 'Unknown', group.observed ? 'neutral' : 'unknown'), `${n(group.missing)} missing availability status; named detail is on Workloads and services.`] })),
      { id: 'nodes', cells: ['Nodes', `${n(at(ctx, 'nodes'))} reported Ready / ${n(at(ctx, 'nodeInventory'))} inventoried`, at(ctx, 'nodeInventory') == null ? 'Expected-node inventory is unavailable; a Ready count alone does not establish complete node coverage.' : 'Kubernetes node inventory is not a configuration inventory of every expected physical node.'] },
      { id: 'cpu', cells: ['CPU requests', f(at(ctx, 'capCpu'), '%'), 'Requested / allocatable CPU; this is scheduling commitment, not current utilization.'] },
      { id: 'memory', cells: ['Memory requests', f(at(ctx, 'capMem'), '%'), 'Requested / allocatable memory; inspect namespace working sets separately.'] },
      { id: 'pods', cells: ['Pod slots', f(at(ctx, 'capPods'), '%'), 'Observed pods / allocatable pod slots; capacity is not readiness.'] },
    ], 'Controller inventory unavailable.'),
    chart('k8s-traffic', 'HTTP requests and 5xx responses', ['requests', 'errors5xx'], 'reqps', 'Six-minute rates. A gap in the error family stays a gap; request traffic does not establish zero errors.'),
    chart('k8s-cpu', 'Measured container CPU', ['cores'], 'cores', 'Container CPU over six minutes. Compare measured usage with requested capacity without treating either as a health score.'),
    protectionSummary(ctx), collection(ctx), nav(ctx),
  ];
}

function workloads(ctx: DomainContext): DomainSection[] {
  return [
    namespaceTable(ctx), serviceTable(ctx),
    ...(ctx.mode === 'live' ? [
      chart('k8s-route-traffic', 'Traffic by route / service', ['routeTrafficHistory'], 'reqps', 'Six-minute rate by exporter route identity.'),
      chart('k8s-route-errors', '5xx responses by route / service', ['routeErrorHistory'], 'reqps', 'Independent error-family observations. Missing errors are not filled with traffic-derived zeroes.'),
    ] : [chart('k8s-workload-traffic', 'Gateway HTTP requests and 5xx responses', ['requests', 'errors5xx'], 'reqps', 'The recording has aggregate traffic history. It cannot be assigned to an individual route or namespace.')]),
    controllerTable(ctx),
    ...(ctx.mode === 'live' ? [
      chart('k8s-namespace-cpu', 'CPU by namespace', ['namespaceCpuHistory'], 'cores', 'Measured CPU by namespace.'),
      chart('k8s-namespace-memory', 'Working memory by namespace', ['namespaceMemoryHistory'], 'bytes', 'Measured container working sets; this is separate from memory requests.'),
      chart('k8s-capacity-commitment', 'Requested capacity', ['cpuCommitHistory', 'memoryCommitHistory'], 'percent', 'CPU and memory requests / allocatable capacity; no new saturation threshold is applied.'),
    ] : [{ kind: 'text' as const, id: 'k8s-workload-history-gap', title: 'History coverage', tone: 'unknown' as const, text: 'Per-route traffic and errors, namespace CPU and capacity-request histories were not captured. The window-end tables remain independent observations; these native histories are available with the live source when the underlying metric families are present.' }]),
    fluxTable(ctx),
    { kind: 'text', id: 'k8s-flux-counts', title: 'Flux aggregate capture', text: `${n(at(ctx, 'fluxReady'))} resources reported ready; ${n(at(ctx, 'fluxFailing'))} reported failing; ${n(at(ctx, 'fluxSuspended'))} reported suspended. The older failure query contains an unconditional zero fallback. Without named inventory, these counts cannot establish complete reconciliation.` },
    certificateTable(ctx),
    { kind: 'links', id: 'k8s-workload-tools', title: 'Native investigation', items: [
      { label: 'Kubernetes / Views / Global', href: ctx.deeper('k8s_views_global') },
      { label: 'Traefik routes', href: ctx.deeper('n5bu_kv4k') },
      { label: 'Flux control plane', href: ctx.deeper('flux-control-plane') },
      { label: 'Certificate management', href: ctx.deeper('cdhrcds8aosg0c') },
    ] },
  ];
}

function storage(ctx: DomainContext): DomainSection[] {
  return [
    protectionSummary(ctx), databaseTable(ctx),
    { kind: 'text', id: 'k8s-cnpg-interpretation', title: 'What the database evidence can establish', text: 'Expected pods come from the Barman init-container inventory or CNPG collectors, including pods whose metrics disappear. A Barman timestamp of zero means no completed base backup has been recorded. Missing timestamps mean unknown protection. WAL waiting is a sampled backlog; this page does not establish the 15-minute alert duration. The existing stale-backup rule uses 26 hours and a separate one-hour alert duration.' },
    ...(ctx.mode === 'live' ? [
      chart('k8s-cnpg-backups', 'Database base-backup age', ['dbBackupAgeHistory'], 's', 'Current Barman family, by namespace and pod. Zero timestamps are explained in the instance table and are not plotted as a gigantic age.'),
      chart('k8s-cnpg-wal', 'WAL segments waiting to archive', ['dbWalHistory'], 'short', 'By database pod. A missing exporter remains a gap rather than a zero backlog.'),
    ] : [{ kind: 'text' as const, id: 'k8s-protection-history-gap', title: 'Protection history unavailable in this recording', tone: 'unknown' as const, text: 'Current Barman base-backup age, WAL backlog and named volume-backup histories were not captured. Their native charts use the live source when those metric families are present. No history is synthesized from aggregate backup counts.' }]),
    volumeTable(ctx),
    ...(ctx.mode === 'live' ? [chart('k8s-volume-backups', 'Volume backup age', ['volumeBackupAgeHistory'], 's', 'By claim and volume. Compare with the volume policy; this does not establish CNPG recovery or restore verification.')] : []),
    collection(ctx),
    { kind: 'links', id: 'k8s-storage-tools', title: 'Continue protection investigation', items: [
      { label: 'Longhorn Operations', href: ctx.deeper('longhorn-operations') },
      { label: 'CloudNativePG instances', href: ctx.deeper('cloudnative-pg') },
      { label: 'Athena storage and hardware', href: ctx.href({ focus: 'Athena', entity: null }) },
      { label: 'Proxmox backup evidence', href: ctx.href({ focus: 'Proxmox', entity: null }) },
    ] },
  ];
}

/** The detail route carries an explicit typed identity; it never falls back to all-resource totals. */
function resource(ctx: DomainContext): DomainSection[] {
  const identity = resourceIdentity(ctx.entity);
  const key = ctx.entity;
  const unknown = (): DomainSection[] => [{ kind: 'text', id: 'k8s-resource-missing', title: 'Resource observations unavailable', tone: 'unknown', text: 'This exact identity has no observations in the selected source and range. Unknown resources are not assigned another resource’s totals.', links: [{ label: 'Workloads and services', href: ctx.href({ focus: 'Kubernetes', view: 'workloads', entity: null }) }, { label: 'Storage and protection', href: ctx.href({ focus: 'Kubernetes', view: 'storage', entity: null }) }] }];
  if (!identity) return key ? unknown() : [{ kind: 'text', id: 'k8s-select-resource', title: 'Choose a named resource', text: 'Open a namespace, controller, route, database instance, volume or certificate from the domain tables. Its detail stays in the same range and source.' }, nav(ctx)];
  const header = (name: string, description: string): DomainSection => ({ kind: 'text', id: 'k8s-resource-identity', title: name, text: description });
  const links = (items: Array<{ label: string; href: string }>): DomainSection => ({ kind: 'links', id: 'k8s-resource-next', title: 'Investigate this resource', items });
  if (identity.kind === 'namespace') {
    const row = namespaces(ctx).find((item) => item.key === key); if (!row) return unknown();
    return [header(row.name, 'Namespace context at the end of the selected window. Controller and pod observations do not establish an external service assertion.'), namespaceTable(ctx, [row]), controllerTable(ctx, row.controllers), links([{ label: `Native namespace / ${row.name}`, href: ctx.deeper(NAMESPACE_DASHBOARD, { namespace: row.name, cluster: 'k8s-prod' }) }, { label: 'Cluster context', href: ctx.deeper('k8s_views_global') }])];
  }
  if (identity.kind === 'controller') {
    const row = controllers(ctx).find((item) => item.key === key); if (!row) return unknown();
    return [header(`${row.namespace}/${row.name}`, `${row.kind}. ${row.evidence}. Desired replicas are configuration; a zero desired count is not proof of service availability.`), controllerTable(ctx, [row]), links([{ label: 'Inspect namespace and pods', href: ctx.deeper(NAMESPACE_DASHBOARD, { namespace: row.namespace, cluster: 'k8s-prod' }) }, { label: 'Flux reconciliation', href: ctx.deeper('flux-control-plane') }])];
  }
  if (identity.kind === 'service') {
    const row = services(ctx).find((item) => item.key === key); if (!row) return unknown();
    return [header(row.name, 'This is the normalized Traefik exporter service identity. The capture does not supply an authoritative route-to-namespace or controller mapping. A route with no observed requests has no latency estimate.'), serviceTable(ctx, [row]), links([{ label: `Traefik / ${row.name}`, href: ctx.deeper('n5bu_kv4k', { service: nativeServiceFilter(row.name) }) }, { label: 'Network paths', href: ctx.href({ focus: 'Network', entity: null }) }])];
  }
  if (identity.kind === 'database') {
    const row = databases(ctx).find((item) => item.key === key); if (!row) return unknown();
    return [header([row.namespace, row.pod].filter(Boolean).join('/'), 'An individual CNPG instance, not a unique database total. Namespace is kept unknown when the old recording omitted it. Inspect completed Backup objects, plugin logs and the backing store when backup evidence is absent.'), databaseTable(ctx, [row]), links([{ label: `CloudNativePG / ${row.pod}`, href: ctx.deeper('cloudnative-pg', { ...(row.namespace ? { namespace: row.namespace } : {}), instances: row.pod }) }, { label: 'Athena backing store', href: ctx.href({ focus: 'Athena', entity: null }) }])];
  }
  if (identity.kind === 'volume') {
    const row = volumes(ctx).find((item) => item.key === key); if (!row) return unknown();
    return [header([row.namespace, row.pvc ?? row.volume].filter(Boolean).join('/'), `Longhorn volume: ${row.volume}. Replica robustness, volume backup freshness and database recovery are independent checks.`), volumeTable(ctx, [row]), links([{ label: 'Inspect claim in Longhorn Operations', href: ctx.deeper('longhorn-operations', { ...(row.namespace ? { namespace: row.namespace } : {}), ...(row.pvc ? { pvc: row.pvc } : {}) }) }])];
  }
  if (identity.kind === 'certificate') {
    const row = certificates(ctx).find((item) => item.key === key); if (!row) return unknown();
    return [header(row.name, 'Expiry and lifetime remaining are observations. Renewal-controller status, issuance errors and trust-chain behavior require separate evidence.'), certificateTable(ctx, [row]), links([{ label: 'Inspect certificate management', href: ctx.deeper('cdhrcds8aosg0c', row.namespace ? { namespace: row.namespace } : undefined) }])];
  }
  const row = fluxResources(ctx).find((item) => item.key === key); if (!row) return unknown();
  return [header(`${row.namespace}/${row.name}`, `${row.kind}. A suspended resource is configured to pause; check its context before treating it as a failure.`), fluxTable(ctx, [row]), links([{ label: 'Inspect Flux control plane', href: ctx.deeper('flux-control-plane') }])];
}

const controllerRequired = ['ksmUp', 'ksmAge', 'controllerInventory', 'controllerTotal', 'controllerReady', 'controllerMissing'].map(ref);
export const domain: DomainDefinition = {
  id: 'k8s', focus: 'Kubernetes', title: 'Kubernetes', accent: 'azure', queries,
  pages: [
    { id: 'overview', title: 'Operations', question: 'Which service or protection path needs attention, which resource is affected, and what should I inspect next?', required: controllerRequired, sections: overview },
    { id: 'workloads', title: 'Workloads and services', question: 'Which namespace, controller, route or reconciliation explains the symptom?', required: [...controllerRequired, ref('nsCpu'), ref('routeRate')], sections: workloads },
    { id: 'storage', title: 'Storage and protection', question: 'What is known about volume backups and database recovery, and which instance or claim needs investigation?', required: ['backupExpected', 'backupMissing', 'longhornCoverage', 'longhornAge', 'dbExpected', 'dbBackupTimestamp'].map(ref), sections: storage },
    { id: 'resource', title: 'Resource detail', question: 'What evidence belongs to this exact resource, and where do I continue investigating?', required: [], sections: resource },
  ],
};
