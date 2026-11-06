import type { DomainCell, DomainContext, DomainDefinition, DomainSection } from '../contract';
import { format } from '../data';
import { backups, DAILY_JOB, guests, hosts, pools, queries, ref, scalar, type Guest, type Host } from './model';

const number = (value: number | null, suffix = '', digits = 1) => format(value, digits, suffix);
const bytes = (value: number | null) => value == null ? 'Unknown' : number(value / 1024 ** 3, ' GiB');
const nodeHref = (ctx: DomainContext, node: string) => ctx.href({ focus: 'Proxmox', view: 'node', entity: node });
const guestHref = (ctx: DomainContext, guest: Guest) => ctx.href({ focus: 'Proxmox', view: 'guest', entity: guest.entity });
const membershipText = (guest: Guest) => ({ included: 'In a backup job', excluded: 'In no backup job', unknown: 'Membership unknown' }[guest.membership]);
const stamp = (ctx: DomainContext, at: number | null) => at == null ? 'Unavailable' : new Intl.DateTimeFormat('en-US', { timeZone: ctx.timeZone, dateStyle: 'medium', timeStyle: 'short' }).format(at * 1000);
const state = (up: number | null, yes: string, no: string): DomainCell => ({ text: up === 1 ? yes : up === 0 ? no : 'Unknown', tone: up === 0 ? 'neutral' : up === 1 ? 'neutral' : 'unknown' });
const chart = (id: string, title: string, key: string, unit: string, description: string, entityLabels?: string[]): DomainSection => ({ kind: 'chart', id, title, refs: [ref(key)], unit, description, height: 240, ...(entityLabels ? { entityLabels } : {}) });

function boundaries(ctx: DomainContext): DomainSection {
  return {
    kind: 'text', id: 'evidence-boundary', title: 'Cluster, host and backup evidence',
    text: ctx.mode === 'recorded'
      ? 'This recording identifies guests and their placement, guest CPU/memory, pool occupancy and host collection. It omits VMIDs, boot policy, per-guest backup membership and completion samples. No recording here establishes current runtime intent, archive retention or restorability.'
      : 'The PVE API covers pve-sbx; separate host collectors describe the physical nodes. Guest runtime state, host collection, scheduled membership and backup completion answer different questions. Desired current runtime state and restorability are not measured.',
  };
}

function hostTable(ctx: DomainContext, selected = hosts(ctx)): DomainSection {
  return {
    kind: 'table', id: 'nodes', title: 'Nodes and guest placement',
    description: 'The three expected nodes remain visible when telemetry disappears. A working host collector does not prove API availability or quorum. CPU is mean busy time over 5 minutes.',
    columns: ['Node / detail', 'Host collector', 'PVE API state', 'CPU', 'Host memory occupied', 'Guests observed'],
    rows: selected.map((host) => ({ id: host.name, cells: [
      { text: host.name, href: nodeHref(ctx, host.name) }, state(host.collection, 'Scrape observed', 'Scrape failed'), state(host.apiUp, 'Up reported', 'Down reported'), number(host.cpu, '%'), number(host.memory, '%'),
      guests(ctx).filter((guest) => guest.node === host.name).map((guest) => guest.name).join(', ') || 'None in observed inventory',
    ] })), empty: 'No node identity available.',
  };
}
function guestTable(ctx: DomainContext, selected = guests(ctx)): DomainSection {
  return {
    kind: 'table', id: 'guests', title: 'Guests and runtime context',
    description: 'Non-template guests. Stopped is an observed state and may be intentional. Start-at-boot is a configuration policy; it does not establish the desired state now. Memory is guest usage / configured memory.',
    columns: ['Guest / detail', 'Node', 'Observed', 'Desired now', 'Start at boot', 'CPU', 'Memory used'],
    rows: selected.map((guest) => ({ id: guest.key, cells: [
      { text: guest.name, href: guestHref(ctx, guest), detail: [guest.type, guest.id].filter(Boolean).join(' · ') || undefined },
      guest.node ? { text: guest.node, href: nodeHref(ctx, guest.node) } : 'Unknown',
      state(guest.up, 'Running', 'Stopped'), 'Not measured', state(guest.onboot, 'Enabled', 'Disabled'), number(guest.cpu, '%'), number(guest.memory, '%'),
    ] })), empty: 'No guest identity observed. This does not prove that the cluster has no guests.',
  };
}
function poolTable(ctx: DomainContext, node?: string): DomainSection {
  const selected = pools(ctx).filter((pool) => !node || pool.node === node || pool.node === 'shared');
  return {
    kind: 'table', id: 'pools', title: 'Local and shared storage pools',
    description: 'Pool occupancy is separate from guest disk allocation. Shared NFS pools appear once per share; shares may expose the same backing capacity, so their percentages must not be added. Storage type and free bytes were not captured.',
    columns: ['Pool', 'Scope', 'Occupied', 'Investigate'], rows: selected.map((pool) => ({ id: pool.key, cells: [pool.storage,
      pool.node === 'shared' ? 'Shared NFS' : { text: pool.node, href: nodeHref(ctx, pool.node) }, number(pool.used, '%'),
      { text: 'Native storage detail', href: ctx.deeper('Dp7Cd57Zza', pool.node === 'shared' ? undefined : { instance: pool.node }) },
    ] })), empty: 'No pool observations in this window. No free-space conclusion is available.',
  };
}
function backupTable(ctx: DomainContext, selected = backups(ctx)): DomainSection {
  return {
    kind: 'table', id: 'completions', title: 'Job completion evidence · trailing 48 hours',
    description: `Per-node job results evaluated at the selected range end · ${ctx.timeZone}. Each outcome retains its own timestamp. These are rolling latest results, not a complete task ledger.`,
    columns: ['Node', 'Job', 'Latest observed outcome', 'Last clean completion', 'Last error completion', 'Daily policy'],
    rows: selected.map((result) => ({ id: result.key, cells: [
      { text: result.node, href: nodeHref(ctx, result.node) }, result.job,
      { text: ({ unknown: 'Unavailable', clean: 'Clean completion', failed: 'Completed with errors', retried: 'Clean completion after error' })[result.outcome], tone: result.outcome === 'failed' ? 'warning' : result.outcome === 'unknown' ? 'unknown' : 'neutral', detail: result.failureVisibility ? 'The error remains within the documented backup alert condition. Alert delivery is not measured here.' : undefined },
      stamp(ctx, result.success), stamp(ctx, result.failure),
      ({ unknown: 'No clean completion available', 'within-policy': 'Clean completion within 30h', 'outside-policy': 'Last clean completion ≥30h ago', 'not-daily': 'Does not satisfy daily-backups' })[result.dailyFreshness],
    ] })), empty: 'No completion identities observed.',
  };
}
function protectionLimits(ctx: DomainContext): DomainSection {
  return {
    kind: 'text', id: 'backup-limits', title: 'Membership → completion → recovery evidence', tone: 'unknown',
    text: `${ctx.mode === 'recorded' ? 'Completion telemetry was not captured and remains unavailable in recorded mode. ' : ''}Membership says a guest belongs to at least one job; it does not identify the job or prove a run completed. A clean ${DAILY_JOB} result is per node and job, not a per-guest archive receipt. Missing results can mean no run, a lost notification or collection loss. Retained archive age, integrity and restore drills are unavailable here. A manual success does not satisfy the scheduled job.`,
  };
}
function tools(ctx: DomainContext, host?: Host, guest?: Guest): DomainSection {
  const node = host?.name ?? guest?.node;
  return { kind: 'links', id: 'investigate', title: 'Continue investigating', items: [
    { label: node ? `Proxmox · ${node}` : 'Proxmox cluster and storage', href: ctx.deeper('Dp7Cd57Zza', node ? { instance: node } : undefined), description: guest ? `Node-scoped native dashboard; guest ${guest.name} (${guest.id ?? 'VMID not captured'}) remains identified on this detail page.` : 'Native cluster, guest and storage detail in the same source and range.' },
    { label: node ? `${node} host metrics` : 'PVE host metrics', href: ctx.deeper('hosts', { platform: 'pve', ...(node ? { node } : {}) }), description: 'Host collection, pressure, filesystems and network.' },
    { label: 'Drive health', href: ctx.deeper('f8f249a0-be78-41b1-97fe-8d0a92a71b93', { platform: 'pve', ...(node ? { instance: node } : {}) }), description: 'Drive evidence is independent of pool occupancy and backup completion.' },
    { label: 'Backup membership and results', href: ctx.href({ focus: 'Proxmox', view: 'backups', entity: null }), description: 'Compare scheduled coverage with sparse job outcomes.' },
  ] };
}
function guestCharts(): DomainSection[] {
  return [
    chart('guest-cpu', 'Guest CPU', 'guestCpuHistory', 'percentunit', 'Reported CPU ratio per guest; missing samples remain gaps. No range recording is available.', ['id', 'name']),
    chart('guest-memory', 'Guest memory usage', 'guestMemoryHistory', 'bytes', 'Reported guest memory usage. This is separate from host memory and allocated guest disk space. No range recording is available.', ['id', 'name']),
  ];
}
function overview(ctx: DomainContext): DomainSection[] {
  const quorum = scalar(ctx, 'quorum');
  return [boundaries(ctx), {
    kind: 'metrics', id: 'cluster-context', title: 'Cluster coordination', items: [
      { label: ctx.mode === 'recorded' ? 'Captured quorum flag' : 'PVE quorum', value: quorum === 1 ? 'Quorate reported' : quorum === 0 ? 'Not quorate reported' : 'Unknown', tone: quorum === 0 ? 'warning' : quorum == null ? 'unknown' : 'neutral', detail: 'Quorum is independent of guest runtime state and backup outcomes.' },
      { label: 'Guest inventory', value: guests(ctx).length ? `${guests(ctx).length} observed` : 'Unavailable', detail: 'Observed non-template identities, not guaranteed complete cluster membership.', href: ctx.href({ focus: 'Proxmox', view: 'guests', entity: null }) },
      { label: 'Backup completions', value: backups(ctx).some((result) => result.outcome !== 'unknown') ? 'Review per-node outcomes' : 'Unavailable', detail: 'No result is inferred from job membership.', href: ctx.href({ focus: 'Proxmox', view: 'backups', entity: null }) },
    ],
  }, hostTable(ctx), guestTable(ctx), poolTable(ctx),
  ...(ctx.mode === 'live' ? [chart('host-cpu', 'Host CPU · PVE nodes', 'hostCpuHistory', 'percent', 'Host CPU busy percent over 5 minutes, independently by node.')] : []), tools(ctx)];
}
function guestsPage(ctx: DomainContext): DomainSection[] {
  return [guestTable(ctx), { kind: 'table', id: 'guest-resources', title: 'Allocation and protection context', columns: ['Guest', 'Reported uptime', 'Memory used / configured', 'Allocated disk', 'Job membership'], rows: guests(ctx).map((guest) => ({ id: guest.key, cells: [
    { text: guest.name, href: guestHref(ctx, guest) }, guest.uptime == null ? 'Unknown' : number(guest.uptime / 86400, ' days'), `${bytes(guest.memoryBytes)} / ${bytes(guest.memorySize)}`, bytes(guest.diskSize), membershipText(guest),
  ] })), empty: 'No non-template guests observed.' }, ...guestCharts(), protectionLimits(ctx), tools(ctx)];
}
function backupsPage(ctx: DomainContext): DomainSection[] {
  return [protectionLimits(ctx), {
    kind: 'table', id: 'membership', title: 'Guest membership in scheduled backup jobs',
    description: `Membership requires a guest-level observation or a complete inventory reporting no omissions. Missing guest-level evidence alone does not establish inclusion.${ctx.mode === 'recorded' ? ' Membership was not captured in this recording.' : ''}`,
    columns: ['Guest', 'Node', 'Membership', 'Archive / restore evidence'], rows: guests(ctx).map((guest) => ({ id: guest.key, cells: [
      { text: guest.name, href: guestHref(ctx, guest) }, guest.node ? { text: guest.node, href: nodeHref(ctx, guest.node) } : 'Unknown', { text: membershipText(guest), tone: guest.membership === 'excluded' ? 'warning' : guest.membership === 'unknown' ? 'unknown' : 'neutral' }, 'Unavailable',
    ] })), empty: 'Guest membership unavailable; no protected-guest count can be established.',
  }, backupTable(ctx), ...(ctx.mode === 'live' ? [chart('completion-history', 'Latest completion timestamp by node, job and outcome', 'backupCompletionHistory', 'dateTimeAsIso', 'Rolling maximum over 48 hours, not a count of runs. Success and error are separate series; notification gaps cannot establish success.')] : []), poolTable(ctx), tools(ctx)];
}
function guestPage(ctx: DomainContext): DomainSection[] {
  if (!ctx.entity) return [guestTable(ctx)];
  const found = guests(ctx).filter((guest) => guest.entity === ctx.entity);
  if (found.length !== 1) return [{ kind: 'text', id: 'guest-unavailable', title: 'Guest identity unavailable', tone: 'unknown', text: 'This identity is missing or ambiguous in the selected inventory. Another guest’s measurements cannot fill this page.', links: [{ label: 'Observed guests', href: ctx.href({ focus: 'Proxmox', view: 'guests', entity: null }) }] }];
  const guest = found[0];
  return [{ kind: 'text', id: 'guest-identity', title: guest.name, text: `Cluster pve-sbx · ${guest.type ?? 'type unknown'} · ${guest.id ?? 'VMID not captured'} · node ${guest.node ?? 'unknown'}. Tags: ${guest.tags ?? 'not captured'}. Desired current runtime state is not measured; a stopped guest is not automatically an incident.` }, guestTable(ctx, [guest]), {
    kind: 'metrics', id: 'guest-allocation', title: 'Resource context at window end', items: [
      { label: 'Memory used', value: bytes(guest.memoryBytes), detail: `${number(guest.memory, '%')} of configured memory` },
      { label: 'Configured memory', value: bytes(guest.memorySize) },
      { label: 'Allocated disk', value: bytes(guest.diskSize), detail: 'Disk allocation does not measure the VM filesystem’s free space.' },
      { label: 'Reported uptime', value: guest.uptime == null ? 'Unknown' : number(guest.uptime / 86400, ' days'), detail: 'No lifecycle events are inferred from uptime.' },
      { label: 'Backup membership', value: membershipText(guest), href: ctx.href({ focus: 'Proxmox', view: 'backups', entity: null }) },
    ],
  }, ...guestCharts(), ...(guest.node ? [backupTable(ctx, backups(ctx).filter((result) => result.node === guest.node)), poolTable(ctx, guest.node)] : []), protectionLimits(ctx), tools(ctx, undefined, guest)];
}
function nodePage(ctx: DomainContext): DomainSection[] {
  if (!ctx.entity) return [hostTable(ctx)];
  const host = hosts(ctx).find((item) => item.name === ctx.entity);
  if (!host) return [{ kind: 'text', id: 'node-unavailable', title: 'Node identity unavailable', tone: 'unknown', text: 'This node is neither in the expected policy inventory nor observed in this range.' }];
  return [hostTable(ctx, [host]), { kind: 'metrics', id: 'host-evidence', title: 'Collection context', items: [{ label: 'Host scrape-status sample age', value: number(host.age, ' s'), detail: 'Age at the selected range end; this does not establish backup notification delivery.' }] }, guestTable(ctx, guests(ctx).filter((guest) => guest.node === host.name)),
    chart('node-cpu', `${host.name} · host CPU`, 'hostCpuHistory', 'percent', 'Mean host busy time over 5 minutes. Detail is scoped to this node.', ['instance']),
    chart('node-memory', `${host.name} · available host memory`, 'hostMemoryHistory', 'bytes', 'Host memory available; no recorded range is available.', ['instance']),
    poolTable(ctx, host.name), backupTable(ctx, backups(ctx).filter((result) => result.node === host.name)), protectionLimits(ctx), tools(ctx, host)];
}

export const domain: DomainDefinition = {
  id: 'pve', focus: 'Proxmox', title: 'Proxmox', accent: 'sea', queries,
  pages: [
    { id: 'overview', title: 'Overview', question: 'Where are the guests, and which evidence needs a closer look?', required: ['guestInfo', 'hostUp'].map(ref), sections: overview },
    { id: 'guests', title: 'Guests', question: 'What is each guest doing, on which node, with which resources?', required: ['guestInfo', 'guestUp'].map(ref), sections: guestsPage },
    { id: 'backups', title: 'Backups', question: 'Do guests belong to a job, and did the scheduled jobs report a clean completion?', required: ['guestInfo', 'backupCompletion'].map(ref), sections: backupsPage },
    { id: 'guest', title: 'Guest detail', question: 'What can the selected guest’s identity and measurements establish?', required: [ref('guestInfo')], sections: guestPage },
    { id: 'node', title: 'Node detail', question: 'Which guests and pools depend on this node, and what did its jobs report?', required: [ref('hostUp')], sections: nodePage },
  ],
};
