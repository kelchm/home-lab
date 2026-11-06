import type { DomainCell, DomainContext, DomainDefinition, DomainSection } from '../contract';
import { format, records } from '../data';
import { assertion, domainContext, drives, DSM_TARGET, hardwareStatus, incidents, PROBE, queries, raidStatus, ref, signal, volumes, type Assessment, type Drive } from './model';

const DRIVE_HEALTH = 'f8f249a0-be78-41b1-97fe-8d0a92a71b93';
const f = (value: number | null, suffix = '', digits = 1) => format(value, digits, suffix);
const bytes = (value: number | null) => value == null ? 'Unknown' : `${f(value / 1024 ** 4, '', 2)} TiB`;
const cell = (assessment: Assessment): DomainCell => ({ ...assessment });
const route = (ctx: DomainContext, view: string, entity: string | null = null) => ctx.href({ focus: 'Athena', view, entity });
const driveLink = (ctx: DomainContext, drive: string) => route(ctx, 'drive', drive);
const incidentLink = (ctx: DomainContext) => route(ctx, 'incident', PROBE);
const stamp = (ctx: DomainContext, at: number) => new Intl.DateTimeFormat('en-US', { timeZone: ctx.timeZone, month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }).format(at);
const duration = (ms: number) => `${Math.floor(ms / 3600000)}h ${Math.round((ms % 3600000) / 60000)}m`;
const chart = (id: string, title: string, keys: string[], unit: string, description: string, entityLabels?: string[]): Extract<DomainSection, { kind: 'chart' }> => ({
  kind: 'chart', id, title, refs: keys.map(ref), unit, description, height: 225, ...(entityLabels ? { entityLabels } : {}),
});

function navigation(ctx: DomainContext): DomainSection {
  return { kind: 'links', id: 'athena-navigation', title: 'Follow the evidence', items: [
    { label: 'DSM incident investigation', href: incidentLink(ctx), description: 'Assertion, HTTP response, collectors and activity in the selected window.' },
    { label: 'Storage pools and capacity', href: route(ctx, 'storage'), description: 'DSM status and volume occupancy, with missing capacity stated explicitly.' },
    { label: 'Drive bays', href: route(ctx, 'drives'), description: 'Bay identity, DSM assessment, temperature and raw sector counters.' },
    { label: 'Home daily overview', href: ctx.href({ focus: 'Overview', view: undefined, entity: null }) },
  ] };
}

function nativeLinks(ctx: DomainContext, drive?: string): DomainSection {
  return { kind: 'links', id: 'athena-native', title: 'Native investigation', items: [
    { label: 'DSM HTTP probe detail', href: ctx.deeper('pi-blackbox', { job: 'pi-blackbox', target: DSM_TARGET, probe: PROBE }), description: 'The configured NAS login-page assertion; inspect response and timing without inferring a storage outage.' },
    { label: 'Synology storage and hardware', href: ctx.deeper('synology-health'), description: 'Pools, hardware and per-bay activity. The recording aggregates host identity; select the NAS in native detail if needed.' },
    { label: drive ? `Drive health · ${drive}` : 'Native drive health', href: ctx.deeper(DRIVE_HEALTH, { platform: 'synology', ...(drive ? { device: drive } : {}) }), description: drive ? 'Selected bay is carried in the link. The existing native dashboard filters platform/instance, so locate this bay in its table.' : 'Normalized DSM status and missing-health evidence.' },
  ] };
}

function serviceSummary(ctx: DomainContext): DomainSection {
  const check = assertion(signal(ctx, 'assertion', { probe: PROBE }));
  const collector = signal(ctx, 'scrapes');
  return { kind: 'table', id: 'service-and-collection', title: 'Service assertion and collection', columns: ['Evidence at window end', 'Observation', 'What it establishes'], rows: [
    { id: 'dsm', cells: [{ text: 'Athena DSM · login page', href: incidentLink(ctx) }, cell(check), 'The configured HTTP assertion only; independent of storage health.'] },
    { id: 'http', cells: ['DSM response', f(signal(ctx, 'httpCode', { probe: PROBE }), '', 0), 'HTTP status is response context; assertion success is evaluated separately.'] },
    { id: 'duration', cells: ['Probe duration', f(signal(ctx, 'durationMs', { probe: PROBE }), ' ms'), 'One window-end observation, not a latency percentile.'] },
    { id: 'collectors', cells: ['Synology collectors · sampled minimum', collector === 1 ? { text: 'Observed collectors scraping', tone: 'neutral' } : collector === 0 ? { text: 'A collector scrape failed', tone: 'warning' } : { text: 'Unknown', tone: 'unknown' }, 'Minimum of observed up series; missing expected collectors cannot be counted from this aggregate.'] },
  ], empty: 'No service observations.' };
}

function incidentTable(ctx: DomainContext): DomainSection {
  const events = incidents(ctx);
  return { kind: 'table', id: 'dsm-incidents', title: 'DSM assertion episodes', description: `Sampled failure spans · ${ctx.timeZone}. A gap ends a span without establishing recovery.`, columns: ['Investigation', 'Sampled interval', 'Duration', 'HTTP context', 'Collector context'], rows: events.map((event) => ({ id: String(event.start), cells: [
    { text: event.resolved ? 'Resolved assertion failure' : 'Failure · recovery unconfirmed', href: incidentLink(ctx), tone: event.resolved ? 'neutral' : 'warning', detail: event.clipped ? 'Start clipped by selected range' : event.startUncertain ? 'Start follows missing observations' : undefined },
    `${stamp(ctx, event.start)} – ${stamp(ctx, event.end)}`, `${duration(event.duration)}${event.startUncertain || !event.resolved ? ' · observed span' : ''}`,
    event.http404 ? 'HTTP 404 in sampled failure window' : 'Not established as a 404 episode',
    event.collectorContinuity ? 'Sampled collector continuity held' : 'Continuity not established',
  ] })), empty: 'No failure episodes established by available samples. Missing assertion history does not prove an incident-free window.' };
}

function activity(): DomainSection {
  return chart('nas-activity', 'NAS disk activity', ['read', 'written'], 'Bps', 'Aggregate read and written bytes/s over trailing 6-minute windows. This is activity, not proof of filesystem access or application availability.');
}
function alignedEvidence(): DomainSection[] {
  return [
    { ...chart('dsm-assertion', 'DSM login-page assertion', ['assertionHistory'], 'short', '1 = passing, 0 = assertion failed. Minute-scale history establishes episode boundaries; gaps remain unknown.', ['probe']), entity: PROBE },
    chart('dsm-response', 'DSM HTTP response', ['dsmStatus'], 'short', 'Five-minute HTTP status observations. A 404 means an HTTP response was received; it does not explain why the login-page assertion failed.'),
    chart('nas-collectors', 'Synology collector continuity', ['scrapes'], 'short', 'Minimum observed collector up value. 1 = sampled scrapes succeeded, 0 = at least one observed scrape failed. An absent collector is not an observed zero.'),
    chart('dsm-duration', 'DSM probe duration', ['dsmSeconds'], 's', 'Five-minute duration observations, separate from the HTTP response and assertion result.'), activity(),
  ];
}

function hardware(ctx: DomainContext): DomainSection {
  return { kind: 'table', id: 'hardware-assessments', title: 'DSM hardware assessment at window end', columns: ['Component', 'DSM report', 'Scope'], rows: [
    { id: 'system', cells: ['System', cell(hardwareStatus(signal(ctx, 'system'))), 'Aggregate DSM system status'] },
    { id: 'power', cells: ['Power', cell(hardwareStatus(signal(ctx, 'power'))), 'Power supply assessment, independent of DSM HTTP assertion'] },
    { id: 'fan', cells: ['Fans', cell(hardwareStatus(signal(ctx, 'fan'))), 'Aggregate system/CPU fan assessment'] },
    { id: 'temperature', cells: ['System temperature', f(signal(ctx, 'temp'), ' °C'), 'Measurement only; no new threshold assigned'] },
  ], empty: 'No hardware observations.' };
}

function driveTable(ctx: DomainContext, rows: Drive[]): DomainSection {
  return { kind: 'table', id: 'drive-bays', title: 'Drive bays at window end', description: 'Rows come from observed bay identities. Missing bays are not inferred; status and health are separate DSM assessments. Raw model names do not establish disk capacity.', columns: ['Bay / detail', 'Model', 'DSM status', 'Health assessment', 'Temperature', 'Capacity'], rows: rows.map((drive) => ({ id: drive.id, cells: [
    { text: drive.id, href: driveLink(ctx, drive.id) }, drive.model ?? 'Unknown', cell(drive.status), cell(drive.health), f(drive.temperature, ' °C'), bytes(drive.capacity),
  ] })), empty: 'No bay identities observed. This does not establish that the NAS has no drives.' };
}
function sectors(ctx: DomainContext, rows: Drive[]): DomainSection {
  const counter = (value: number | null): DomainCell => ({ text: f(value, '', 0), tone: value == null ? 'unknown' : value > 0 ? 'warning' : 'neutral' });
  return { kind: 'table', id: 'drive-sectors', title: 'Raw sector counters', description: 'Observed zeros are retained as zeros. Missing counter families remain unknown; these counters alone do not establish drive or backup health.', columns: ['Bay', 'Reallocated', 'Pending', 'Uncorrectable'], rows: rows.map((drive) => ({ id: drive.id, cells: [{ text: drive.id, href: driveLink(ctx, drive.id) }, counter(drive.reallocated), counter(drive.pending), counter(drive.uncorrectable)] })), empty: 'No drive counter observations.' };
}
function driveCharts(selected = false): DomainSection[] {
  const labels = selected ? ['disk'] : undefined;
  return [
    chart('drive-health', 'Drive health history', ['driveHealthHistory'], 'short', 'Normalized DSM assessment: 0 = failure, 1 = healthy report, 2 = warning. History was not captured in the daily recording.', labels),
    chart('drive-temperature', 'Drive temperatures', ['driveTempHistory'], 'celsius', 'Per-bay temperature history; not captured in recorded mode.', labels),
    chart('drive-busy', 'Drive I/O busy time', ['driveBusy'], 'percentunit', 'Share of time with I/O in flight over 5 minutes. Native sataN devices map to DSM Disk N; aggregate NAS activity is not substituted.', labels),
    chart('drive-throughput', 'Drive read and write activity', ['driveRead', 'driveWritten'], 'Bps', 'Per-bay trailing 5-minute rates. Missing samples remain unavailable, including in the recording.', labels),
  ];
}
function protection(): DomainSection {
  return { kind: 'text', id: 'nas-protection', title: 'Protection evidence is incomplete', tone: 'unknown', text: 'NAS backup completion and restore verification were not captured. Pool status, drive assessments and Kubernetes Longhorn backup counts cannot establish Athena backup health.' };
}

function overview(input: DomainContext): DomainSection[] {
  const ctx = domainContext(input);
  return [serviceSummary(ctx), incidentTable(ctx), hardware(ctx), activity(), protection(), navigation(ctx), nativeLinks(ctx)];
}
function storage(input: DomainContext): DomainSection[] {
  const ctx = domainContext(input);
  const pools = [...new Set(records(ctx.series, ref('raidStates')).map((s) => s.labels.raidName).filter(Boolean))];
  return [
    { kind: 'table', id: 'storage-pools', title: 'Pool and volume state', description: 'The recording provides one aggregate Storage Pool status. Named pool/volume status is queried in live mode; it was not captured.', columns: ['Resource', 'DSM state', 'Evidence'], rows: [
      { id: 'pool-aggregate', cells: ['Storage Pool aggregate', cell(raidStatus(signal(ctx, 'raid'))), 'Maximum captured DSM status code across matching Storage Pool names; names and membership omitted.'] },
      ...pools.map((name) => ({ id: name, cells: [name, cell(raidStatus(signal(ctx, 'raidStates', { raidName: name }))), 'Named DSM report'] })),
    ], empty: 'No pool observations.' },
    { kind: 'table', id: 'storage-capacity', title: 'Volume capacity', description: 'Percentage used is captured independently of absolute capacity. No capacity is inferred from model numbers, drive count or RAID assumptions.', columns: ['Volume', 'Used', 'Total', 'Free'], rows: volumes(ctx).map((volume) => ({ id: volume.id, cells: [volume.id, f(volume.usedPercent, '%'), bytes(volume.total), bytes(volume.free)] })), empty: 'No volume identity or capacity observations.' },
    chart('volume-occupancy', 'Volume occupancy', ['volumeHistory'], 'percent', 'Used percentage by named volume. Historical occupancy was not captured; the window-end percentage is not expanded into a fabricated trend.'),
    activity(), protection(), navigation(ctx), nativeLinks(ctx),
  ];
}
function driveBays(input: DomainContext): DomainSection[] {
  const ctx = domainContext(input);
  const rows = drives(ctx);
  return [driveTable(ctx, rows), sectors(ctx, rows), { kind: 'text', id: 'drive-capacity-gap', title: 'Drive detail coverage', tone: 'unknown', text: 'Per-drive capacity is not established by the available Synology telemetry or capture. Per-bay activity and temperature/status histories are live queries with no recorded samples. Window-end drive assessments do not describe the earlier DSM incident.' }, ...driveCharts(), navigation(ctx), nativeLinks(ctx)];
}
function selectedDrive(input: DomainContext): DomainSection[] {
  const ctx = domainContext(input);
  if (!ctx.entity) return [{ kind: 'links', id: 'choose-drive', title: 'Select a drive bay', items: drives(ctx).map((drive) => ({ label: drive.id, href: driveLink(ctx, drive.id), description: drive.model ?? 'Model unknown' })) }];
  const drive = drives(ctx).find((item) => item.id === ctx.entity);
  if (!drive) return [{ kind: 'text', id: 'drive-unavailable', title: 'Drive identity unavailable', tone: 'unknown', text: 'This bay has no identity or assessment observations in the selected range. Another bay’s values are not substituted.', links: [{ label: 'All drive bays', href: route(ctx, 'drives') }] }];
  return [driveTable(ctx, [drive]), sectors(ctx, [drive]), { kind: 'text', id: 'drive-detail-limits', title: 'What this bay evidence establishes', text: 'Health and fault status are separate DSM assessments. Capacity and per-bay historical activity were not captured. A model number does not establish installed capacity; aggregate NAS throughput does not describe this bay.' }, ...driveCharts(true), nativeLinks(ctx, drive.id), navigation(ctx)];
}
function incident(input: DomainContext): DomainSection[] {
  const ctx = domainContext(input);
  if (ctx.entity && ctx.entity !== PROBE) return [{ kind: 'text', id: 'incident-unavailable', title: 'Incident identity unavailable', tone: 'unknown', text: 'This resource is not the Athena DSM probe.', links: [{ label: 'Athena DSM investigation', href: incidentLink(ctx) }] }];
  return [
    { kind: 'text', id: 'incident-scope', title: 'Investigate the DSM assertion', text: 'The check asks whether the NAS login-page assertion passes. HTTP 404 responses show that an HTTP endpoint answered. They do not establish the cause of the assertion failure or a NAS outage. Compare collector and activity histories independently; window-end storage assessments cannot diagnose an earlier failure.', links: [{ label: 'Focus the Athena DSM assertion', href: incidentLink(ctx) }, { label: 'Home daily overview', href: ctx.href({ focus: 'Overview', view: undefined, entity: null }) }] },
    serviceSummary(ctx), incidentTable(ctx), ...alignedEvidence(),
    { kind: 'text', id: 'incident-resolution', title: 'Evidence boundaries', text: 'Episode duration uses minute-scale assertion samples. HTTP and Synology collector captures are five-minute observations, so their transition times can differ. A passing sample establishes assertion recovery; a gap does not. Collector continuity does not prove filesystem availability, expected collector membership, or backup protection.' },
    nativeLinks(ctx), navigation(ctx),
  ];
}

export const domain: DomainDefinition = {
  id: 'nas', focus: 'Athena', title: 'Athena', accent: 'azure', queries,
  pages: [
    { id: 'overview', title: 'Overview', question: 'Did the DSM service assertion fail, or does storage need investigation?', required: ['assertion', 'httpCode', 'scrapes', 'system', 'raid', 'diskInfo', 'diskHealth'].map(ref), sections: overview },
    { id: 'storage', title: 'Storage', question: 'What does DSM report about pools and volume capacity?', required: ['raid', 'volumes'].map(ref), sections: storage },
    { id: 'drives', title: 'Drives', question: 'Which bay has a fault, warning, or missing assessment?', required: ['diskInfo', 'diskHealth', 'diskStatus', 'diskTemp'].map(ref), sections: driveBays },
    { id: 'incident', title: 'DSM incident', question: 'What failed during the DSM assertion episode, and what remained observable?', required: ['assertionHistory', 'dsmStatus', 'scrapes'].map(ref), sections: incident },
    { id: 'drive', title: 'Selected drive', question: 'What is known about this bay, and which evidence needs native investigation?', required: ['diskInfo', 'diskHealth', 'diskStatus', 'diskTemp'].map(ref), sections: selectedDrive },
  ],
};
