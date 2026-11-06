import type { DomainCell, DomainContext, DomainDefinition, DomainSection } from '../contract';
import { format } from '../data';
import { collection, devicePorts, devices, paths, ports, queries, radios, reading, ref, registration, type Collection, type NetworkDevice, type NetworkPath } from './model';

const f = (value: number | null, unit = '', digits = 1) => format(value, digits, unit);
const scaled = (value: number | null, divisor: number) => value == null ? null : value / divisor;
const internal = (ctx: DomainContext, view: string, entity: string | null) => ctx.href({ focus: 'Network', view, entity });
const pathCell = (ctx: DomainContext, path: NetworkPath): DomainCell => ({ text: path.name, href: internal(ctx, 'path', path.name), detail: path.group ? `Controller group ${path.group}` : 'Controller group unknown' });
const deviceCell = (ctx: DomainContext, device: NetworkDevice): DomainCell => ({ text: device.name, href: internal(ctx, 'device', device.name), detail: `${device.type} · ${device.version ?? 'version unknown'}` });
const linkState = (path: NetworkPath): DomainCell => ({
  text: path.state === 'online' ? 'Reported online' : path.state === 'offline' ? 'Reported offline' : 'State unknown',
  tone: path.state === 'offline' ? 'bad' : path.state === 'unknown' ? 'unknown' : 'neutral',
  detail: path.age == null ? 'Metric age unknown' : `Metric age ${f(path.age, ' s')}`,
});
const collectorCell = (state: Collection): DomainCell => ({ text: { observed: 'Scrape observed', failed: 'Scrape failed', unknown: 'Unknown' }[state], tone: state === 'failed' ? 'warning' : state === 'unknown' ? 'unknown' : 'neutral' });
const chart = (id: string, title: string, keys: string[], unit: string, description: string, entityLabels?: string[]): DomainSection => ({
  kind: 'chart', id, title, refs: keys.map(ref), unit, description, height: 245, ...(entityLabels ? { entityLabels } : {}),
});

function context(ctx: DomainContext): DomainSection {
  return { kind: 'text', id: 'network-context', title: 'Locate the symptom', text: `${ctx.mode === 'recorded' ? 'These are historical observations from the selected recording.' : 'Observations follow the selected live window.'} Compare WAN monitors, then gateway traffic and the local device or radio. A reported online link does not prove successful failover. Port counters have no confirmed provider mapping; eth8 and eth9 remain port identities.` };
}

function collectionTable(ctx: DomainContext): DomainSection {
  return { kind: 'table', id: 'network-collection', title: 'Collection context', description: 'A failed scrape describes collection, not an offline WAN or device. These collector queries were not captured in the recording; metric observations remain separate evidence.', columns: ['Collector', 'Scrape', 'What it observes'], rows: [
    { id: 'unifi', cells: ['UniFi API', collectorCell(collection(ctx, 'unifi')), 'WAN monitors, device inventory, ports and radios'] },
    { id: 'dish', cells: ['Starlink API', collectorCell(collection(ctx, 'dish')), 'Dish throughput, ping, drop and obstruction'] },
    { id: 'tmobile', cells: ['T-Mobile API', collectorCell(collection(ctx, 'tmobile')), '5G registration and radio signal'] },
  ], empty: 'Collector status unavailable.' };
}

function pathTable(ctx: DomainContext, items = paths(ctx)): DomainSection {
  return { kind: 'table', id: 'wan-paths', title: 'WAN paths at window end', description: 'Monitor latency and availability describe the controller’s checks. Provisioned rates are provider-reported capacity, not measured traffic or a speed test.', columns: ['Path', 'Link report', 'Monitor latency', 'Monitor availability', 'Provisioned down / up'], rows: items.map((path) => ({ id: path.name, cells: [
    pathCell(ctx, path), linkState(path), f(path.latency == null ? null : path.latency * 1000, ' ms'), f(path.availability, '%'),
    `${f(scaled(path.provisionedDown, 1e6), ' Mbit/s')} / ${f(scaled(path.provisionedUp, 1e6), ' Mbit/s')}`,
  ] })), empty: 'No path identities observed. Link state is unknown.' };
}

function gatewayPorts(ctx: DomainContext): DomainSection {
  return { kind: 'table', id: 'gateway-ports', title: 'Gateway WAN counters by port', description: 'RX and TX are from the gateway’s perspective, as trailing 6-minute rates. The capture aggregates by port across gateways and omits provider identity. Do not read these rows as Starlink or T-Mobile throughput.', columns: ['Port', 'Receive · window end', 'Transmit · window end', 'Provider mapping'], rows: ports(ctx).map((port) => ({ id: port.id, cells: [
    { text: port.id, href: internal(ctx, 'port', port.id) }, f(scaled(port.rx, 1e6), ' MB/s'), f(scaled(port.tx, 1e6), ' MB/s'), { text: 'Unconfirmed', tone: 'unknown' },
  ] })), empty: 'No gateway port samples. Traffic is unknown, not zero.' };
}

function tools(ctx: DomainContext, device?: NetworkDevice): DomainSection {
  // Native dashboards use case-sensitive AP/Switch/Gateway variables. Escape literal names for their regex selectors.
  const exact = device ? device.name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') : undefined;
  const ap = device?.type === 'uap' || device?.type === 'radio';
  const variable: Record<string, string> | undefined = ap ? { AP: exact! } : /^(udm|usg|ugw|uxg)$/.test(device?.type ?? '') ? { Gateway: exact! } : device ? { Switch: exact! } : undefined;
  const primary = ap ? ['Access point radios and VAPs', 'g5wFWqxZk'] : /^(udm|usg|ugw|uxg)$/.test(device?.type ?? '') ? ['Gateway counters and monitors', '4Yo8IZ-Wk'] : ['Switch ports and errors', 'FsfxpWaZz'];
  return { kind: 'links', id: 'network-tools', title: 'Investigate in native dashboards', items: [
    ...(device ? [{ label: `${device.name} · ${primary[0]}`, href: ctx.deeper(primary[1], variable), description: 'The selected device, time window and source follow this link.' }] : [
      { label: 'UniFi sites', href: ctx.deeper('9WaGWZaZk'), description: 'Site and subsystem traffic.' },
      { label: 'Gateway', href: ctx.deeper('4Yo8IZ-Wk'), description: 'WAN/LAN counters, errors and monitor details.' },
      { label: 'Switches', href: ctx.deeper('FsfxpWaZz'), description: 'Per-port rates, errors and drops.' },
      { label: 'Access points', href: ctx.deeper('g5wFWqxZk'), description: 'Radio utilization, VAPs and wireless traffic.' },
    ]),
    { label: 'UniFi clients', href: ctx.deeper('jMfvAjxWz', ap ? { AP: exact! } : device?.type === 'usw' ? { Switch: exact! } : undefined), description: 'Inspect individual wired or wireless clients; device client counts are not additive.' },
    { label: 'Network flows', href: ctx.deeper('unifi-flows'), description: 'Native flow investigation. Flow samples are not included in this recording.' },
  ] };
}

function pathsPage(ctx: DomainContext): DomainSection[] {
  return [context(ctx), pathTable(ctx),
    chart('wan-latency', 'WAN monitor latency', ['latencyHistory'], 's', 'Controller monitor latency by labelled WAN path; this is separate from dish POP ping.'),
    gatewayPorts(ctx),
    chart('gateway-rx', 'Gateway WAN receive', ['wanRxHistory'], 'Bps', 'Trailing 6-minute byte rates by port; no provider attribution.'),
    chart('gateway-tx', 'Gateway WAN transmit', ['wanTxHistory'], 'Bps', 'Trailing 6-minute byte rates by port; no provider attribution.'),
    { kind: 'links', id: 'local-segment', title: 'Continue along the local segment', items: [{ label: 'Devices & radios', href: internal(ctx, 'devices', null), description: 'Compare clients, load and channel utilization without assuming the WAN caused the symptom.' }] },
    collectionTable(ctx), tools(ctx),
  ];
}

function radioTable(ctx: DomainContext, name?: string): DomainSection {
  const items = radios(ctx).filter((radio) => !name || radio.name === name);
  return { kind: 'table', id: 'network-radios', title: name ? `${name} · radios` : 'Radios and channel context', description: 'Channel busy includes neighbours’ traffic. Band and channel are separate identities; a missing utilization or client query stays unknown. A radio’s absence does not prove it is disabled.', columns: ['AP / radio', 'Band', 'Channel', 'Channel busy', 'Clients'], rows: items.map((radio) => ({ id: radio.id, cells: [
    radio.name ? { text: radio.name, href: internal(ctx, 'device', radio.name), detail: radio.id } : { text: radio.id, detail: 'AP identity unavailable', tone: 'unknown' },
    radio.band ? `${radio.band} GHz` : 'Unknown', f(radio.channel, '', 0), f(radio.busy, '%'), f(radio.clients, '', 0),
  ] })), empty: 'No radio observations for this selection. Collection or radio capability is unknown.' };
}

function devicesPage(ctx: DomainContext): DomainSection[] {
  return [
    { kind: 'text', id: 'inventory-context', title: 'Local segment evidence', text: 'Rows join independently observed device identity, resource and radio queries. Missing fields remain unknown; a device absent from every query cannot be counted as online or offline. Device metrics do not prove end-to-end reachability. Client counts can overlap between gateway, switches and APs.' },
    { kind: 'table', id: 'network-devices', title: 'Devices at window end', columns: ['Device', 'Observation', 'Clients', 'CPU', 'Memory', 'Temperature', 'Uptime'], rows: devices(ctx).map((device) => ({ id: device.name, cells: [
      deviceCell(ctx, device), { text: device.identityObserved && device.metricsObserved ? 'Identity + metrics' : device.metricsObserved ? 'Metrics only' : device.identityObserved ? 'Identity only' : 'No current samples', tone: !device.metricsObserved ? 'unknown' : 'neutral' },
      f(device.clients, '', 0), f(device.cpu, '%'), f(device.memory, '%'), f(device.temperature, ' °C'), f(scaled(device.uptime, 86400), ' d'),
    ] })), empty: 'No device inventory. Missing collection is not an offline-device diagnosis.' },
    radioTable(ctx), collectionTable(ctx), tools(ctx),
  ];
}

function choose(ctx: DomainContext, kind: 'path' | 'device' | 'port'): DomainSection[] {
  const items = kind === 'path' ? paths(ctx).map((path) => path.name) : kind === 'device' ? devices(ctx).map((device) => device.name) : ports(ctx).map((port) => port.id);
  return [
    ...(ctx.entity ? [{ kind: 'text' as const, id: 'entity-unavailable', title: 'Selection unavailable', tone: 'unknown' as const, text: `No ${kind} identity matches this selection in the available evidence. Choose a resource below; unrelated resources are not substituted.` }] : []),
    { kind: 'links', id: 'choose-resource', title: `Select a ${kind}`, items: items.map((entity) => ({ label: entity, href: internal(ctx, kind, entity) })) },
    ...(!items.length ? [{ kind: 'text' as const, id: 'no-resource', title: 'No resource observations', text: 'This window has no resource identities. Missing data does not establish an idle network.', tone: 'unknown' as const }] : []),
  ];
}

function pathPage(ctx: DomainContext): DomainSection[] {
  const path = paths(ctx).find((path) => path.name === ctx.entity);
  if (!path) return choose(ctx, 'path');
  const result: DomainSection[] = [context(ctx), pathTable(ctx, [path]),
    chart('path-latency', `${path.name} · monitor latency`, ['latencyHistory'], 's', 'Only the selected WAN label; an absent label produces no series.', ['wan']),
    chart('path-state', `${path.name} · reported link state`, ['wanStateHistory'], 'short', '1 = reported online, 0 = reported offline; freshness-filtered gaps remain unknown. This is not a failover test.', ['wan']),
  ];
  if (path.name === 'Starlink (WAN2)') result.push(
    { kind: 'table', id: 'dish-context', title: 'Dish diagnostics at window end', columns: ['POP ping', 'Ping drop · trailing 15m', 'Obstructed · trailing 1h', 'Dish uptime'], rows: [{ id: 'dish', cells: [f(reading(ctx, 'dishPing'), ' ms'), f(reading(ctx, 'dishDrop'), '%'), f(reading(ctx, 'dishObstructed'), '%'), f(scaled(reading(ctx, 'dishUptime'), 86400), ' d')] }], empty: 'Dish diagnostics unavailable.' },
    chart('dish-ping', 'Dish POP ping', ['dishPingHistory'], 'ms', 'Dish-local latency to its POP; do not equate it with controller WAN monitor latency.'),
    chart('dish-rx', 'Dish downlink', ['dishDownHistory'], 'bps', 'Measured dish throughput in bits/s, independently of gateway port counters.'),
    chart('dish-tx', 'Dish uplink', ['dishUpHistory'], 'bps', 'Measured dish throughput in bits/s; no gateway port mapping is assumed.'),
  );
  if (path.name === 'T-Mobile (WAN1)') result.push(
    { kind: 'table', id: 'cellular-context', title: '5G diagnostics at window end', description: 'No fixed signal thresholds or bar scale are assumed. A zero from the captured registration fallback cannot prove “not registered”.', columns: ['Registration evidence', 'Reported bars', 'RSRP', 'SINR', 'Gateway uptime'], rows: [{ id: 'tmobile', cells: [registration(ctx), f(reading(ctx, 'tmobileBars'), '', 0), f(reading(ctx, 'tmobileRsrp'), ' dBm'), f(reading(ctx, 'tmobileSinr'), ' dB'), f(scaled(reading(ctx, 'tmobileUptime'), 86400), ' d')] }], empty: '5G diagnostics unavailable.' },
    chart('cellular-rsrp', '5G reference signal power', ['tmobileRsrpHistory'], 'dBm', 'RSRP over the selected range; gaps do not imply loss of registration.'),
    chart('cellular-sinr', '5G signal to interference and noise', ['tmobileSinrHistory'], 'dB', 'SINR is radio context, not a throughput or failover measurement.'),
  );
  result.push({ kind: 'text', id: 'path-traffic-boundary', title: 'Traffic attribution', text: 'The gateway recording has port-labelled RX/TX counters but no confirmed mapping to this provider. Inspect gateway ports separately; no provider traffic is inferred from port order.', links: [{ label: 'Gateway port traffic', href: internal(ctx, 'paths', null) }] }, collectionTable(ctx), tools(ctx));
  return result;
}

function devicePage(ctx: DomainContext): DomainSection[] {
  const device = devices(ctx).find((device) => device.name === ctx.entity);
  if (!device) return choose(ctx, 'device');
  const ap = device.type === 'uap' || device.type === 'radio';
  const gateway = /^(udm|usg|ugw|uxg)$/.test(device.type);
  const result: DomainSection[] = [
    { kind: 'text', id: 'device-identity', title: device.name, text: `${device.type} · firmware ${device.version ?? 'unknown'}. ${device.identityObserved ? 'Device identity observed.' : 'No current device info observation; independent metric labels retain this row.'} ${device.metricsObserved ? 'Some resource measurements are available.' : 'Resource measurements are unknown.'} UniFi API scrape: ${collectorCell(collection(ctx, 'unifi')).text}. None of these observations is a direct reachability check.` },
    { kind: 'table', id: 'device-resources', title: 'Resource context at window end', columns: ['Clients', 'CPU', 'Memory', 'Temperature', 'Uptime'], rows: [{ id: device.name, cells: [f(device.clients, '', 0), f(device.cpu, '%'), f(device.memory, '%'), f(device.temperature, ' °C'), f(scaled(device.uptime, 86400), ' d')] }], empty: 'Resource measurements unavailable.' },
    { kind: 'text', id: 'device-history-coverage', title: 'History coverage', tone: ctx.mode === 'recorded' ? 'unknown' : 'neutral', text: ctx.mode === 'recorded' ? 'Per-device load, per-port traffic and radio histories were not captured. The native charts below remain unavailable in recorded mode; the captured window-end tables are independent observations.' : 'Native charts use existing UniFi device, port and radio metrics. Missing families or labels remain empty. RX/TX refer to the selected device’s counters; different interfaces or VAPs must not be added as independent end-to-end traffic.' },
    chart('device-cpu', `${device.name} · CPU`, ['deviceCpuHistory'], 'percent', 'CPU utilization by selected device.', ['name']),
    chart('device-memory', `${device.name} · memory`, ['deviceMemHistory'], 'percent', 'Memory utilization by selected device.', ['name']),
  ];
  if (ap) result.push(
    radioTable(ctx, device.name),
    chart('radio-busy', 'Channel utilization', ['radioBusyHistory'], 'percent', 'Total busy airtime by labelled radio; includes neighbouring traffic.', ['name']),
    chart('radio-rx', 'Radio receive utilization', ['radioReceiveHistory'], 'percent', 'Native UniFi receive airtime ratio, expressed as a percentage; not traffic volume.', ['name']),
    chart('radio-tx', 'Radio transmit utilization', ['radioTransmitHistory'], 'percent', 'Native UniFi transmit airtime ratio, expressed as a percentage.', ['name']),
    chart('vap-rx', 'Wireless receive traffic', ['vapRxHistory'], 'Bps', 'Receive bytes/s by AP and radio, summed across VAP counters; distinct from airtime.', ['name']),
    chart('vap-tx', 'Wireless transmit traffic', ['vapTxHistory'], 'Bps', 'Transmit bytes/s by AP and radio, summed across VAP counters.', ['name']),
  );
  else result.push(
    { kind: 'table', id: 'device-ports', title: 'Ports · window end', description: 'Port identifiers are scoped to this device. A port name is descriptive context, not a verified provider or path mapping.', columns: ['Port ID', 'Port name', 'Receive', 'Transmit'], rows: devicePorts(ctx, device.name).map((port) => ({ id: port.id, cells: [port.id, port.name ?? 'Unknown', f(scaled(port.rx, 1e6), ' MB/s'), f(scaled(port.tx, 1e6), ' MB/s')] })), empty: 'Per-device port history is unavailable in this window. Missing counters are not zero traffic.' },
    chart('device-rx', 'Port receive traffic', ['portRxHistory'], 'Bps', 'Only ports carrying the selected device name; port_id, port_num and port_name labels retain their context.', ['name']),
    chart('device-tx', 'Port transmit traffic', ['portTxHistory'], 'Bps', 'Transmit rate by selected device and port.', ['name']),
    ...(gateway ? [
      chart('device-wan-rx', 'Gateway WAN receive traffic', ['deviceWanRxHistory'], 'Bps', 'Device-labelled WAN counters. These are separate from LAN port counters and have no assumed provider mapping.', ['name']),
      chart('device-wan-tx', 'Gateway WAN transmit traffic', ['deviceWanTxHistory'], 'Bps', 'Device-labelled WAN transmit counters.', ['name']),
    ] : []),
  );
  result.push(tools(ctx, device));
  return result;
}

function portPage(ctx: DomainContext): DomainSection[] {
  const port = ports(ctx).find((port) => port.id === ctx.entity);
  if (!port) return choose(ctx, 'port');
  return [
    { kind: 'text', id: 'port-identity', title: `Gateway port ${port.id}`, text: 'These gateway WAN counters were aggregated by port across gateways. Provider and device identity were not retained. A counter row cannot identify which ISP carried a connection or prove failover; use the selected port to investigate traffic independently.' },
    { kind: 'table', id: 'port-counters', title: 'Window-end rates · trailing 6m', columns: ['Port', 'Receive', 'Transmit', 'Provider'], rows: [{ id: port.id, cells: [port.id, f(scaled(port.rx, 1e6), ' MB/s'), f(scaled(port.tx, 1e6), ' MB/s'), 'Unconfirmed'] }], empty: 'Port counters unavailable.' },
    chart('port-rx', `${port.id} · receive`, ['wanRxHistory'], 'Bps', 'RX from the gateway perspective; only this port label.', ['port']),
    chart('port-tx', `${port.id} · transmit`, ['wanTxHistory'], 'Bps', 'TX from the gateway perspective; no provider inference.', ['port']),
    { kind: 'links', id: 'port-next', title: 'Continue investigation', items: [{ label: 'All paths and gateway ports', href: internal(ctx, 'paths', null) }, { label: 'Device and radio evidence', href: internal(ctx, 'devices', null) }] }, tools(ctx),
  ];
}

export const domain: DomainDefinition = {
  id: 'net', focus: 'Network', title: 'Network', accent: 'sea', queries,
  pages: [
    { id: 'paths', title: 'Paths', question: 'Which WAN path or gateway port explains the symptom?', required: ['wanState', 'wanAge', 'latency'].map(ref), sections: pathsPage },
    { id: 'devices', title: 'Devices & radios', question: 'Which local device or radio needs closer inspection?', required: ['deviceInfo', 'deviceCpu', 'deviceMem', 'deviceUptime', 'radioChannel', 'radioBusy', 'radioClients'].map(ref), sections: devicesPage },
    { id: 'path', title: 'Path detail', question: 'What does this path report, and what do its provider diagnostics add?', required: ['wanState', 'wanAge', 'latency'].map(ref), sections: pathPage },
    { id: 'device', title: 'Device detail', question: 'Do local resource, port or radio observations explain this device’s activity?', required: ['deviceInfo', 'deviceCpu', 'deviceMem', 'deviceUptime'].map(ref), sections: devicePage },
    { id: 'port', title: 'Port detail', question: 'How did this gateway port’s RX and TX change in the selected range?', required: ['wanRxHistory', 'wanTxHistory'].map(ref), sections: portPage },
  ],
};
