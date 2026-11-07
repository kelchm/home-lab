import type { DomainCell, DomainContext, DomainDefinition, DomainSection } from '../contract';
import { format } from '../data';
import { hosts, PIS, queries, recovery, ref, timeService, timeServices, timeSources, type SyncState } from './model';

const hostHref = (ctx: DomainContext, entity: string) => ctx.href({ focus: 'Devices', view: 'host', entity });
const scrape = (state: string): DomainCell => ({ text: state, tone: state === 'Scrape failed' ? 'warning' : state === 'Unknown' ? 'unknown' : 'neutral' });
const sync = (state: SyncState): DomainCell => ({ text: state, tone: state === 'Unknown' ? 'unknown' : state === 'Tracking synchronized' ? 'neutral' : 'warning' });

function inventory(ctx: DomainContext): DomainSection {
  return { kind: 'table', id: 'pi-inventory', title: 'Supporting Pis', description: 'Named hosts stay visible when collection is absent. CPU is mean busy time over 5 minutes; memory is used / total. Measurements describe resource use, not service readiness.', columns: ['Host / detail', 'Role', 'Node collector', 'CPU', 'Memory used', 'CPU temperature', 'Scrape age'], rows: hosts(ctx).map((host) => ({ id: host.id, cells: [
    { text: host.id, href: hostHref(ctx, host.id) }, host.role, scrape(host.collection), format(host.cpu, 1, '%'), format(host.memory, 1, '%'), format(host.temperature, 1, ' °C'), format(host.age, 0, ' s'),
  ] })), empty: 'No supporting host identity available.' };
}

function ntpTable(ctx: DomainContext): DomainSection {
  return { kind: 'table', id: 'time-service', title: 'Time service', description: ctx.mode === 'recorded' ? 'Chrony samples were not captured. These are configured server identities, with unknown synchronization and source age.' : 'Collection and tracking are evaluated independently of node-exporter. Sync follows the existing NTP dashboard rules; it is not a calibrated UTC accuracy claim.', columns: ['Server / host detail', 'Tracking state', 'Stratum', 'Reference age', 'Tracking address'], rows: timeServices(ctx).map((server) => ({ id: server.instance, cells: [
    { text: server.instance, href: hostHref(ctx, server.instance) }, sync(server.state), format(server.stratum, 0), format(server.referenceAge, 0, ' s'), server.source ?? 'Unknown',
  ] })), empty: 'No time-server observations.' };
}

function consoleTable(ctx: DomainContext): DomainSection {
  const kvm = recovery(ctx);
  return { kind: 'table', id: 'recovery-console', title: 'Recovery console · GLKVM', description: 'Exporter reachability is the available signal. Successful collection does not prove usable video, keyboard, mouse or a working recovery procedure. Recorded values aggregate the glkvm-kvmd job; the host label was not retained.', columns: ['Console / detail', 'Metrics endpoint', 'CPU', 'Memory used', 'CPU temperature', 'Video / input test'], rows: [{ id: 'glkvm', cells: [
    { text: 'glkvm', href: hostHref(ctx, 'glkvm') }, scrape(kvm.collection), format(kvm.cpu, 1, '%'), format(kvm.memory, 1, '%'), format(kvm.temperature, 1, ' °C'), { text: 'Not observed', tone: 'unknown' },
  ] }], empty: 'Recovery console observations unavailable.' };
}

function chart(key: string, title: string, unit: string, description: string, selected = false): DomainSection {
  return { kind: 'chart', id: key, title, refs: [ref(key)], unit, height: 230, description, ...(selected ? { entityLabels: ['instance'] } : {}) };
}

function tools(ctx: DomainContext, host?: string): DomainSection {
  return { kind: 'links', id: 'device-tools', title: 'Continue investigating', items: [
    { label: host && host !== 'glkvm' ? `${host} / native host metrics` : 'Native host metrics', href: ctx.deeper('hosts', { platform: 'pi', ...(host && host !== 'glkvm' ? { node: host } : {}) }), description: 'Collector and resource detail in the selected source and time range.' },
    { label: 'NTP / synchronization and sources', href: ctx.deeper('ntp', host && timeServices(ctx).some((server) => server.instance === host) ? { server: host } : undefined), description: 'Native chrony tracking, uncertainty and source histories.' },
    { label: 'GLKVM / recovery console metrics', href: ctx.deeper('glkvm-console'), description: 'Metrics reachability and resource history; operational recovery remains untested here.' },
    { label: 'Probes / explicit reachability checks', href: ctx.deeper('pi-blackbox'), description: 'Inspect the actual probe assertion and target before inferring service behavior.' },
  ] };
}

function overview(ctx: DomainContext): DomainSection[] {
  return [inventory(ctx), ntpTable(ctx), consoleTable(ctx),
    ...(ctx.mode === 'live' ? [chart('piTempHistory', 'Supporting Pi CPU temperatures', 'celsius', 'Per-host CPU thermal sensors. A missing sensor is not a cool host.')] : []),
    chart('kvmTempHistory', 'Recovery console temperature', 'celsius', 'GLKVM job aggregate. This history measures thermals, not interactive recovery usability.'), tools(ctx),
  ];
}

function hostPage(ctx: DomainContext): DomainSection[] {
  if (!ctx.entity) return [inventory(ctx), consoleTable(ctx), tools(ctx)];
  if (ctx.entity === 'glkvm') return [consoleTable(ctx),
    chart('kvmTempHistory', 'GLKVM CPU temperature', 'celsius', 'Aggregate over the glkvm-kvmd collector job; no host label was retained in the recording.'),
    ...(ctx.mode === 'live' ? [chart('kvmCpuHistory', 'GLKVM CPU busy', 'percent', 'Reported CPU utilization.'), chart('kvmMemHistory', 'GLKVM memory used', 'percent', 'Reported memory utilization.')] : []), tools(ctx, 'glkvm')];
  const host = hosts(ctx).find((item) => item.id === ctx.entity);
  if (!host) return [{ kind: 'text', id: 'host-unknown', title: 'Host identity unavailable', tone: 'unknown', text: 'This host is neither configured as a supporting Pi nor present in the observed Pi inventory.', links: [{ label: 'Supporting devices', href: ctx.href({ focus: 'Devices', view: 'host', entity: null }) }] }];
  const ntp = timeService(ctx, host.id);
  return [
    { kind: 'text', id: 'host-identity', title: host.id, text: `${host.role}. Node collector: ${host.collection}. Service collection is separate from host collection; resource availability does not establish a functioning UPS collector, ADS-B receiver, streamer or time server.` },
    { kind: 'metrics', id: 'host-resources', title: 'Window-end host observations', items: [
      { label: 'CPU busy · 5m', value: format(host.cpu, 1, '%') }, { label: 'Memory used', value: format(host.memory, 1, '%') },
      { label: 'CPU thermal sensor', value: format(host.temperature, 1, ' °C'), detail: 'No model-independent temperature threshold is assumed.' }, { label: 'Node scrape age', value: format(host.age, 0, ' s') },
    ] },
    ...(host.timeServer ? [{ kind: 'metrics' as const, id: 'host-time', title: 'Time service on this host', items: [
      { label: 'Tracking state', value: ntp.state, tone: sync(ntp.state).tone }, { label: 'Reference age', value: format(ntp.referenceAge, 0, ' s') }, { label: 'Tracking address', value: ntp.source ?? 'Unknown' },
    ] }] : []),
    chart('cpuHistory', 'Host CPU busy · 5m', 'percent', 'Only this selected instance; missing samples remain gaps.', true),
    ...(ctx.mode === 'live' ? [chart('memoryHistory', 'Host memory used', 'percent', 'Only this selected instance.', true), chart('piTempHistory', 'Host CPU temperature', 'celsius', 'Only this host CPU thermal sensor.', true)] : [{ kind: 'text' as const, id: 'host-history-gaps', title: 'History coverage', tone: 'unknown' as const, text: 'The recording contains CPU history. Pi memory and thermal history, service checks and chrony observations were not captured; window-end resource values above do not fill those gaps.' }]),
    ...(host.id === 'ups-compute-rack' || host.id === 'ups-office' ? [{ kind: 'links' as const, id: 'collected-ups', title: 'UPS collected by this host', items: [{ label: host.id === 'ups-office' ? 'Office UPS' : 'Compute rack UPS', href: ctx.href({ focus: 'Power', view: 'unit', entity: host.id === 'ups-office' ? 'office' : 'compute-rack' }), description: 'Follow the same source and time range to load, runtime and input state.' }] }] : []), tools(ctx, host.id),
  ];
}

function timePage(ctx: DomainContext): DomainSection[] {
  return [ntpTable(ctx),
    { kind: 'table', id: 'ntp-sources', title: 'NTP source observations', description: 'Reach counts responses in the last eight polls; source age is time since its last sample. An upstream claim of sync does not establish absolute UTC accuracy.', columns: ['Server', 'Source address', 'Reported source state', 'Polls answered / 8', 'Sample age'], rows: timeSources(ctx).map((source) => ({ id: source.id, cells: [
      { text: source.instance, href: hostHref(ctx, source.instance) }, source.address, source.state, format(source.reach, 0), format(source.age, 0, ' s'),
    ] })), empty: ctx.mode === 'recorded' ? 'Source state, reach and age were not captured. No conclusion about synchronization or upstream reachability is available.' : 'No source observations in this window. This does not establish that no sources are configured.' },
    { kind: 'text', id: 'ntp-rules', title: 'Tracking and collection rules', text: 'The existing NTP dashboard treats a scrape age of five minutes as silent, stratum 0 or 16+ as unsynchronized, and a reference older than one hour as holdover. This preview requires successful exporter and chrony collection plus recent scrape evidence before reporting synchronized tracking. Partial or absent data stays explicit.' },
    chart('offsetHistory', 'NTP last offset', 's', 'Signed tracking offset by server. Uncaptured in recorded mode; absence is not zero offset.'),
    chart('sourceAgeHistory', 'NTP source sample age', 's', 'Per-server, per-source age; missing observations remain gaps.'), tools(ctx),
  ];
}

const hostRequired = ['inventory', 'hostUp', 'cpu', 'memory', 'piTemp', 'kvmUp', 'kvmCpu', 'kvmMem', 'kvmTemp'].map(ref);
const timeRequired = ['ntpUp', 'chronyUp', 'ntpScrapeAge', 'stratum', 'referenceAge'].map(ref);

export const domain: DomainDefinition = {
  id: 'dev', focus: 'Devices', title: 'Devices', accent: 'azure', queries,
  pages: [
    { id: 'overview', title: 'Supporting services', question: 'Are supporting Pis, time service and the recovery metrics endpoint observable?', required: ['inventory', 'hostUp', 'cpu', 'memory', 'piTemp', 'kvmUp', 'kvmCpu', 'kvmMem', 'kvmTemp'].map(ref), sections: overview },
    { id: 'host', title: 'Device inventory & detail', question: 'Which host and resource observations explain this device?', required: [...hostRequired, ...timeRequired], requiredFor: ({ entity }) => PIS.some((host) => host.id === entity && host.timeServer) ? [...hostRequired, ...timeRequired] : hostRequired, sections: hostPage },
    { id: 'time', title: 'Time sources', question: 'Which NTP servers and upstream observations support synchronized tracking?', required: timeRequired, sections: timePage },
  ],
};
