import type { DomainCell, DomainContext, DomainDefinition, DomainScope, DomainSection } from '../contract';
import { format } from '../data';
import { aggregate, deployments, hardware, historicalWindow, HISTORY, HISTORY_LABEL, queries, ref, scale, type CollectorState, type Deployment, type Host, type Signal } from './model';

const DRIVE_HEALTH = 'f8f249a0-be78-41b1-97fe-8d0a92a71b93';
const f = (value: number | null, suffix = '', digits = 1) => format(value, digits, suffix);
const signal = (value: Signal, suffix = '', multiplier = 1) => value.value == null
  ? ({ unsupported: 'Unsupported family', unavailable: 'Unavailable', 'no-observations': 'No observations at window end', ambiguous: 'Ambiguous series', observed: 'Unknown' }[value.state])
  : f(value.value * multiplier, suffix);
const state = (collection: CollectorState): DomainCell => ({
  text: { observed: 'Scrape observed', failed: 'Scrape failed', 'metrics-only': 'Metrics observed', unknown: 'Unknown' }[collection],
  tone: collection === 'failed' ? 'warning' : collection === 'unknown' ? 'unknown' : 'neutral',
  detail: collection === 'metrics-only' ? 'Collector status not captured' : undefined,
});
const hostHref = (ctx: DomainContext, host: string) => ctx.href({ focus: 'Sparks', view: 'host', entity: host });
const deploymentHref = (ctx: DomainContext, deployment: Deployment) => ctx.href({ focus: 'Sparks', view: 'deployment', entity: deployment.key });
const chart = (id: string, title: string, keys: string[], unit: string, description: string, host = false): DomainSection => ({
  kind: 'chart', id, title, refs: keys.map(ref), unit, height: 230, description, ...(host ? { entityLabels: ['instance'] } : {}),
});

function historicalBoundary(ctx: DomainContext): DomainSection {
  const replay = ctx.mode === 'recorded' && historicalWindow(ctx);
  return {
    kind: 'text', id: 'inference-window', title: replay ? `Historical inference · ${HISTORY_LABEL}` : 'Inference observation window',
    text: replay
      ? 'This separate recording describes the selected historical window. Its model, recipe, context pool and stream limit are not facts about the daily overview or the current lab. Instants describe the end of this window.'
      : 'Requests and output follow the selected range. The richer inference recording belongs to a separate, dated window; opening it changes the time range explicitly. Missing targets do not distinguish a stopped deployment from collection loss.',
    links: [{ label: `Open historical recording · ${HISTORY_LABEL}`, href: ctx.href({ view: 'inference', entity: null, from: HISTORY.from, to: HISTORY.to }) }],
  };
}

function hardwareLinks(ctx: DomainContext, host?: string): DomainSection {
  const vars = host ? { node: host } : undefined;
  return { kind: 'links', id: 'hardware-tools', title: 'Investigate hardware', items: [
    { label: 'Sparks / GPU and host health', href: ctx.deeper('sparks-health', vars), description: 'Native GPU, CPU, filesystem and NVMe detail.' },
    { label: 'Hosts', href: ctx.deeper('hosts', { platform: 'spark', ...vars }), description: 'Host metrics in the same time range and source.' },
    { label: 'Drive health', href: ctx.deeper(DRIVE_HEALTH, { platform: 'spark', ...vars }), description: 'Inspect drive evidence; collection is not established by this capture.' },
    { label: 'Inference activity', href: ctx.href({ focus: 'Sparks', view: 'inference', entity: null }), description: 'Check requests and serving identity before attributing hardware load.' },
  ] };
}

function hardwareCharts(host = false): DomainSection[] {
  return [
    chart('memory-history', 'Available unified memory', ['memoryHistory'], 'bytes', 'Available host memory shared by CPU and GPU. Display in binary units (GiB); this is separate from inference context capacity.', host),
    chart('gpu-temperature-history', 'GPU temperature', ['gpuTempHistory'], 'celsius', 'GPU temperature by host. A cool GPU does not imply a cool board.', host),
    chart('board-temperature-history', 'Board thermal-zone peak', ['boardHistory'], 'celsius', 'Highest board thermal-zone temperature in each trailing 6-minute window, independently by host.', host),
    chart('gpu-work', 'GPU utilization', ['gpuUtilHistory'], 'percent', 'Percent utilization is workload context, not a health score or request count.', host),
    chart('power-history', 'GPU power', ['gpuPowerHistory'], 'watt', 'Measured watts. Ordinary power capping is not classified as an incident.', host),
    chart('clock-history', 'SM clock', ['clockHistory'], 'hertz', 'Hz, scaled by Grafana to GHz; trailing 6-minute mean. No fixed ceiling is assumed.', host),
    chart('roce-traffic', 'RoCE transmit traffic', ['roceTraffic'], 'Bps', 'Bytes/s, scaled to MB/s; trailing 6-minute rate on the captured s0f0 interconnect devices. Throughput is not link integrity.', host),
  ];
}

function evidenceSections(ctx: DomainContext, hosts: Host[], selected = false): DomainSection[] {
  const stamp = (time: number) => new Intl.DateTimeFormat('en-US', { timeZone: ctx.timeZone, month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' }).format(time);
  const events = hosts.flatMap((host) => [
    ...host.thermalEvents.map((event, i) => ({ id: `${host.instance}-thermal-${i}`, cells: [host.instance, 'Thermal slowdown', `${stamp(event.start)} – ${stamp(event.end)}`, f(event.peak, ' s'), `${event.samples} samples`] })),
    ...host.roceEvents.map((event, i) => ({ id: `${host.instance}-roce-${i}`, cells: [host.instance, 'RoCE errors', `${stamp(event.start)} – ${stamp(event.end)}`, f(event.peak, ' errors'), `${event.samples} samples`] })),
  ]);
  return [
    { kind: 'table', id: 'pressure-evidence', title: 'Memory stalls, thermal slowdown and link errors', description: 'Window-end observations. Slowdown and errors cover trailing 6-minute windows; overlapping windows must not be added together.', columns: ['Host', 'Memory stalled · 5m', 'Thermal slowdown · 6m', 'RoCE errors · 6m'], rows: hosts.map((host) => ({ id: host.instance, cells: [host.instance, f(scale(host.memoryStalls, 0.01), '%'), f(host.thermalSeconds, ' s'), f(host.roceErrors, ' errors')] })), empty: 'No host selected.' },
    { kind: 'table', id: 'hardware-events', title: 'Observed event windows', description: `Positive sampled windows · ${ctx.timeZone}. Gaps split spans; these are not continuous incident durations. Peak is the largest trailing-window measurement.`, columns: ['Host', 'Evidence', 'Sample span', 'Peak · trailing 6m', 'Coverage'], rows: events, empty: 'No positive event samples available. Missing thermal or error families do not prove an event-free window.' },
    ...(ctx.mode === 'live' ? [
      chart('stalls-history', 'Host memory stalls', ['memoryStalls'], 'percentunit', 'Fraction of wall time fully stalled on memory over 5 minutes.', selected),
      chart('slowdown-history', 'Hardware thermal slowdown', ['thermalSeconds'], 's', 'Seconds in each trailing 6-minute window, independently by host.', selected),
      chart('roce-error-history', 'RoCE error increments', ['roceErrors'], 'short', 'Counter increments in each trailing 6-minute window; gaps remain unknown.', selected),
    ] : [{ kind: 'text' as const, id: 'hardware-capture-limits', title: 'Capture coverage', tone: 'unknown' as const, text: 'GPU scrape status, host memory stalls, per-host thermal slowdown and per-host RoCE error samples were not captured. Host memory and GPU measurements remain usable independently. The older hostless zero totals cannot establish that either host had no events.' }]),
  ];
}

function hardwarePage(ctx: DomainContext): DomainSection[] {
  const hosts = hardware(ctx);
  return [
    { kind: 'text', id: 'hardware-context', title: 'Two hardware hosts, independent collectors', text: 'Start with collection, then compare available memory and thermals. spark-1 and spark-2 are expected host identities; a missing collector does not remove its row. Hardware nodes are not extra inference deployments or requests.' },
    { kind: 'table', id: 'hardware-hosts', title: 'Host evidence at window end', columns: ['Host', 'Host collector', 'GPU collector', 'Available memory', 'GPU temperature', 'Board peak · 6m'], rows: hosts.map((host) => ({ id: host.instance, cells: [{ text: host.instance, href: hostHref(ctx, host.instance) }, state(host.hostCollection), state(host.gpuCollection), f(scale(host.memoryAvailable, 1024 ** 3), ' GiB'), f(host.gpuTemp, ' °C'), f(host.boardTemp, ' °C')] })), empty: 'No host observations.' },
    { kind: 'table', id: 'hardware-load', title: 'Load and headroom', description: 'Reported thermal margin is measured headroom, not a hard-coded GPU temperature ceiling. Filesystem occupancy is the fullest local filesystem; its mountpoint was not captured.', columns: ['Host', 'Host CPU', 'GPU utilization', 'GPU power', 'Thermal margin', 'Fullest filesystem'], rows: hosts.map((host) => ({ id: host.instance, cells: [{ text: host.instance, href: hostHref(ctx, host.instance) }, f(host.cpu, '%'), f(host.gpuUtil, '%'), f(host.gpuPower, ' W'), f(host.gpuMargin, ' °C'), f(scale(host.diskUsed, 0.01), '%')] })), empty: 'No load observations.' },
    ...hardwareCharts(), ...evidenceSections(ctx, hosts), hardwareLinks(ctx),
  ];
}

function hostPage(ctx: DomainContext): DomainSection[] {
  if (!ctx.entity) return [{ kind: 'links', id: 'choose-host', title: 'Select a hardware host', items: hardware(ctx).map((host) => ({ label: host.instance, href: hostHref(ctx, host.instance), description: 'Memory, GPU, board thermals and interconnect evidence.' })) }];
  const host = hardware(ctx).find((item) => item.instance === ctx.entity);
  if (!host) return [{ kind: 'text', id: 'host-not-found', title: 'Host unavailable', text: 'This identity has no observed hardware data in the selected range.', tone: 'unknown', links: [{ label: 'All Sparks hardware', href: ctx.href({ view: 'hardware', entity: null }) }] }];
  return [
    { kind: 'text', id: 'host-identity', title: host.instance, text: `Host collector: ${state(host.hostCollection).text}. GPU collector: ${state(host.gpuCollection).text}. Each collector contributes its own evidence; missing GPU collection does not hide host memory.` },
    { kind: 'metrics', id: 'host-memory', title: 'Memory and filesystem context', items: [
      { label: 'Available unified memory', value: f(scale(host.memoryAvailable, 1024 ** 3), ' GiB'), detail: 'Host memory shared by CPU and GPU.' },
      { label: 'Host CPU', value: f(host.cpu, '%'), detail: 'Mean busy time over 5 minutes.' },
      { label: 'Fullest filesystem', value: f(scale(host.diskUsed, 0.01), '%'), detail: 'Mountpoint identity omitted from recording.', href: ctx.deeper('hosts', { platform: 'spark', node: host.instance }) },
      { label: 'SM clock', value: f(scale(host.clockHz, 1e9), ' GHz'), detail: 'Trailing 6-minute mean.' },
      { label: 'RoCE transmit', value: f(scale(host.roceBytes, 1e6), ' MB/s'), detail: 'Trailing 6-minute rate; s0f0 devices.' },
    ] },
    { kind: 'metrics', id: 'host-thermals', title: 'Board and GPU', items: [
      { label: 'GPU temperature', value: f(host.gpuTemp, ' °C') }, { label: 'Board peak · 6m', value: f(host.boardTemp, ' °C') },
      { label: 'Reported thermal margin', value: f(host.gpuMargin, ' °C') }, { label: 'GPU power', value: f(host.gpuPower, ' W') }, { label: 'GPU utilization', value: f(host.gpuUtil, '%') },
    ] },
    ...hardwareCharts(true), ...evidenceSections(ctx, [host], true), hardwareLinks(ctx, host.instance),
  ];
}

function deploymentTable(ctx: DomainContext, rows: Deployment[]): DomainSection {
  return { kind: 'table', id: 'serving-deployments', title: 'Serving identities observed', description: 'Identity is model + recipe + runtime + head host. A metrics scrape does not establish end-to-end readiness. Tensor-parallel workers are not counted as deployments.', columns: ['Model / detail', 'Recipe', 'Runtime', 'Head host', 'Collection', 'Running / waiting'], rows: rows.map((deployment) => ({ id: deployment.key, cells: [
    { text: deployment.model, href: deploymentHref(ctx, deployment) }, deployment.recipe, deployment.runtime || 'Not captured', { text: deployment.host, href: hostHref(ctx, deployment.host) }, state(deployment.collection),
    `${signal(deployment.running)} / ${signal(deployment.waiting)}`,
  ] })), empty: 'No deployment identity observed at this window. This does not prove intentional shutdown or idle service.' };
}

function contextTable(rows: Deployment[]): DomainSection {
  return { kind: 'table', id: 'inference-capacity', title: 'Request concurrency and context capacity', description: 'Stream capacity comes from the selected recipe. Pool occupancy is 1 − free tokens / total pool tokens; each request has its own context-window limit. Neither describes unified host memory.', columns: ['Recipe / head host', 'Reported streams', 'Context pool occupied', 'Request window', 'Completed-work decode'], rows: rows.map((deployment) => ({ id: deployment.key, cells: [
    `${deployment.recipe} · ${deployment.host}`, signal(deployment.streams), signal(deployment.poolFraction, '%', 100), signal(deployment.contextLength, ' tokens'), signal(deployment.decodeSpeed, ' tok/s'),
  ] })), empty: 'No supported inference capacity observations.' };
}

function inferenceLinks(ctx: DomainContext, deployment?: Deployment): DomainSection {
  return { kind: 'links', id: 'inference-tools', title: 'Continue investigating', items: [
    { label: 'AI / Inference serving', href: ctx.deeper('spark-tensorfold-performance', deployment ? { model: deployment.model, recipe: deployment.recipe, serving_host: deployment.host } : undefined), description: 'Native serving and completed-work detail in the same source and time range.' },
    { label: deployment ? `${deployment.host} hardware` : 'Sparks hardware', href: deployment ? hostHref(ctx, deployment.host) : ctx.href({ view: 'hardware', entity: null }), description: 'Compare host memory, board thermals and RoCE before attributing request pressure.' },
    ...(deployment ? [{ label: 'All inference histories', href: ctx.href({ view: 'inference', entity: null }), description: 'View native charts with each recipe and head-host series identified.' }] : []),
  ] };
}

function inferencePage(ctx: DomainContext): DomainSection[] {
  const rows = deployments(ctx);
  const daily = ctx.mode === 'recorded' && !historicalWindow(ctx);
  const totals = aggregate(ctx);
  return [
    historicalBoundary(ctx), deploymentTable(ctx, rows),
    ...(daily ? [
      { kind: 'metrics' as const, id: 'inference-aggregate', title: 'Across observed inference targets', items: [
        { label: 'Running requests', value: f(totals.running, '', 0), detail: 'Aggregate; deployment labels were not captured.' },
        { label: 'Waiting requests', value: f(totals.waiting, '', 0), detail: 'Zero requires corroboration from the unfilled request history.' },
        { label: 'Largest request-window share', value: f(totals.requestWindowPercent, '%'), detail: 'Maximum anonymous request context / its window. Not pool occupancy.' },
        { label: 'Completion-accounted output', value: f(totals.completedOutput, ' tok/s'), detail: 'Trailing 6-minute average across targets. Not live decoding.' },
      ] },
      chart('running-history', 'Running requests · aggregate', ['runningTotalHistory'], 'short', 'Unfilled request gauges; no attribution to the window-end recipe or TP workers.'),
      chart('waiting-history', 'Waiting requests · aggregate', ['waitingTotalHistory'], 'short', 'Request count, not queue-wait duration. Missing observations remain gaps.'),
      chart('completed-history', 'Output booked on completion', ['completedHistory'], 'suffix:tok/s', 'Aggregate completed output over trailing 6-minute windows; captured at 5-minute steps. Spikes reflect accounting time, not instantaneous decoding.'),
      chart('prompt-history', 'Prompt input booked on completion', ['promptHistory'], 'suffix:tok/s', 'Trailing 6-minute aggregate; cached input is included. This is not live prefill speed.'),
      { kind: 'text' as const, id: 'daily-inference-limits', title: 'Identity and capability gaps', tone: 'unknown' as const, text: 'This daily recording omits runtime identity, labeled request counts, pool occupancy, latency and completed-work decode speed. Its unlabeled maximum stream limit cannot be assigned to a recipe. Open the dated recording for those observations; it will change the time range.' },
    ] : [
      contextTable(rows),
      chart('requests-history', 'Running, waiting and reported stream capacity', ['runningHistory', 'waitingHistory', 'streamsHistory'], 'short', 'Running/waiting use trailing 30-second maxima; streams is the reported recipe limit. Identity remains per recipe, runtime and head host.'),
      chart('pool-history', 'Context pool occupancy', ['poolHistory'], 'percentunit', '1 − free pool tokens / pool capacity; fraction scaled to percent once. Pool allocations may remain occupied while requests are idle.'),
      chart('decode-history', 'Completed-work decode speed', ['decodeHistory'], 'suffix:tok/s', 'Generated tokens / accumulated decode seconds over 5 minutes. This is distinct from aggregate wall-clock output; missing completions leave gaps.'),
      chart('ttft-history', 'Mean time to first token · completed requests', ['ttftHistory'], 's', 'Trailing 2-minute mean. Sparse observations do not establish a percentile or queue-wait duration.'),
      chart('latency-history', 'Mean full-request latency · completed requests', ['latencyHistory'], 's', 'Trailing 2-minute mean. No completions means no observation, not zero latency.'),
      ...(ctx.mode === 'live' ? [
        chart('aggregate-output', 'Aggregate live output · 1m', ['aggregateOutput'], 'suffix:tok/s', 'Monotonic live completion-counter rate, including active work and idle time. The family is optional; unavailable is not idle.'),
        chart('completed-output', 'Completed output · 6m', ['completedOutput'], 'suffix:tok/s', 'Output booked on request completion, independently by recipe and head host. This is not live decoding.'),
      ] : [
        { kind: 'text' as const, id: 'recorded-output-limit', title: 'Aggregate live-output evidence', tone: 'unknown' as const, text: 'This historical recording contains a clamped derivative estimate, without the raw counters needed to verify resets. It is not used as reliable throughput or evidence of idle service. Completed-work decode speed above measures a different quantity; the daily recording separately provides completion-accounted aggregate output.' },
      ]),
    ]),
    { kind: 'text', id: 'inference-gaps', title: 'What this telemetry cannot answer', text: 'Stable sessions, request identity, queue-wait duration, complete deployment membership and end-to-end readiness are not measured. Context entries are anonymous snapshots; their ranks must never be connected into user or session tracks. Another runtime needs its own metric adapter.' },
    inferenceLinks(ctx),
  ];
}

function deploymentPage(ctx: DomainContext): DomainSection[] {
  const rows = deployments(ctx);
  if (!ctx.entity) return [historicalBoundary(ctx), deploymentTable(ctx, rows)];
  const deployment = rows.find((item) => item.key === ctx.entity);
  if (!deployment) return [historicalBoundary(ctx), { kind: 'text', id: 'deployment-not-found', title: 'Deployment identity unavailable', tone: 'unknown', text: 'The exact recipe, runtime and head-host identity has no observations in this window. A replacement recipe is a separate deployment; it cannot fill this detail page.', links: [{ label: 'Observed inference identities', href: ctx.href({ view: 'inference', entity: null }) }] }];
  return [
    historicalBoundary(ctx), deploymentTable(ctx, [deployment]),
    { kind: 'metrics', id: 'deployment-requests', title: 'Requests at window end', items: [
      { label: 'Running requests', value: signal(deployment.running) }, { label: 'Waiting requests', value: signal(deployment.waiting) },
      { label: 'Reported stream limit', value: signal(deployment.streams), detail: 'Recipe-specific capacity. No fixed stream count assumed.' },
      { label: 'Optional health endpoint', value: deployment.healthReported.value === 1 ? 'Reports OK' : deployment.healthReported.value === 0 ? 'Reports not OK' : signal(deployment.healthReported), detail: 'Endpoint report only; not end-to-end readiness.' },
    ] },
    contextTable([deployment]),
    { kind: 'metrics', id: 'deployment-pool', title: 'Pool allocation', items: [
      { label: 'Pool capacity', value: signal(deployment.poolTokens, ' tokens') }, { label: 'Pool free', value: signal(deployment.poolFree, ' tokens') },
      { label: 'Pool occupied', value: signal(deployment.poolFraction, '%', 100), detail: 'Allocations can remain occupied when running requests are zero.' },
    ] },
    { kind: 'table', id: 'anonymous-context', title: 'Anonymous request context at window end', description: 'Largest context first at this instant only. Ranks are not stable identities; no session history or user association is inferred.', columns: ['Instant rank', 'Context tokens', 'Share of request window'], rows: deployment.contexts.map((point) => ({ id: `instant-${point.rank}`, cells: [`#${point.rank}`, f(point.tokens, '', 0), f(scale(point.fraction, 0.01), '%')] })), empty: 'No anonymous request-context observations. This is not evidence of zero occupancy.' },
    { kind: 'metrics', id: 'deployment-performance', title: 'Output and completed-work accounting', items: [
      { label: 'Aggregate live output · 1m', value: signal(deployment.aggregateOutput, ' tok/s'), detail: 'Requires the monotonic live completion-counter family. Not inferred from a clamped derivative.' },
      { label: 'Completed output · 6m', value: signal(deployment.completedOutput, ' tok/s'), detail: 'Per-deployment completed-request accounting. Unlabeled daily totals are not assigned here.' },
      { label: 'Completed-work decode · 5m', value: signal(deployment.decodeSpeed, ' tok/s'), detail: 'Generated tokens / accumulated decode seconds; not aggregate output.' },
      { label: 'Mean first-token latency · 2m', value: signal(deployment.ttft, ' s') }, { label: 'Mean full-request latency · 2m', value: signal(deployment.latency, ' s') },
      { label: 'Cached prompt accounting · 15m', value: signal(deployment.cacheFraction, '%', 100), detail: 'Completed prompt work only.' },
    ] },
    { kind: 'text', id: 'deployment-interpretation', title: 'Keep the evidence separate', text: 'A supported family with no completed-work observation has no latency or decode estimate. An unsupported family needs a different adapter or exporter capability; a capture omission remains unavailable. Zero running requests does not imply an empty pool or a ready endpoint.' },
    inferenceLinks(ctx, deployment),
  ];
}

const inferenceRequired = (scope: DomainScope) => [ref(scope.mode === 'recorded' && historicalWindow(scope) ? 'identity' : 'serving')];

export const domain: DomainDefinition = {
  id: 'spark', focus: 'Sparks', title: 'Sparks', accent: 'orchid', queries,
  pages: [
    { id: 'hardware', title: 'Hardware', question: 'Are both hosts observable, and do memory, board thermals or the interconnect explain the workload?', required: ['hostUp', 'memoryAvailable', 'gpuTemp', 'boardHistory'].map(ref), sections: hardwarePage },
    { id: 'inference', title: 'Inference', question: 'Which deployment is running or waiting, and what explains output or context pressure?', required: ['serving'].map(ref), requiredFor: inferenceRequired, sections: inferencePage },
    { id: 'host', title: 'Host detail', question: 'What evidence belongs to this hardware host, and which collector or resource needs investigation?', required: ['hostUp', 'memoryAvailable'].map(ref), sections: hostPage },
    { id: 'deployment', title: 'Deployment detail', question: 'For this recipe, runtime and head host, what is observed about requests, capacity and completed work?', required: ['serving'].map(ref), requiredFor: inferenceRequired, sections: deploymentPage },
  ],
};
