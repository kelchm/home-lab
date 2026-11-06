import { combine, measured, scale, scoped } from './adapter';
import {
  dailyBands, dailyEpisodes, dailyIdentity, dailyReview, hostRows, instant, scalar, seriesOf, smallestStep, withGaps,
  type Band, type DailyCheck, type DailyWan, type DataLike, type FrameLike, type HostRow, type Point, type Review, type ReviewItem, type Series,
} from './semantics';
import type { Accent, SystemId } from './systems';

export type Span = [number, number];

export interface Row extends ReviewItem {
  system: SystemId;
  spans: Span[];
}

export interface ReviewModel {
  action: Row[];
  recovered: Row[];
  observe: Row[];
  maintenance: Row[];
  confirmed: boolean;
}

export interface Check extends DailyCheck {
  system: SystemId;
  http: number | null;
  ms: number | null;
  bands: Band[];
}

export interface Wan extends DailyWan {
  bands: Band[];
}

export interface Monitor {
  rulesEvaluating: boolean;
  down: number | null;
  notifyFailures: number | null;
  heartbeat: number | null;
}

export interface Marks {
  crit: Span[];
  warn: Span[];
  note: Span[];
  maint: Span[];
}

export interface Lane {
  system: SystemId;
  metric: string;
  unit: string;
  /** The query this lane's own honesty rests on. */
  ref: string;
  color: Accent;
  points: Point[];
  step: number;
  decimals: number;
  max?: number;
  marks: Marks;
  label?: string;
}

/** Queries behind the review with no current answer: the model can't see these, so a quiet result must not be claimed. */
export interface Gaps {
  missing: number;
  retained: number;
  total: number;
}

export interface HomeModel {
  review: ReviewModel | null;
  checks: Check[];
  wans: Wan[];
  hosts: HostRow[];
  monitor: Monitor;
  lanes: Lane[];
  rollup: Band[];
  /** Whether the runtime series behind the notice marks is in the catalog. */
  notices: boolean;
}

const TABS: Record<string, SystemId> = { '⎈-k8s-prod': 'k8s', '▤-pve-sbx': 'pve', '✦-spark': 'spark', '⛁-athena': 'nas', '⇄-network': 'net', '⚡︎-power': 'power', '⌨︎-devices': 'dev' };
const PROBES: Record<string, SystemId> = { 'athena-dsm': 'nas', 'unifi-gateway': 'net' };
const SERVICES_DEFAULT: SystemId = 'k8s';

/** Expected inventories: losing one leaves a gap instead of quietly changing what the lane means. */
const PVE_HOSTS = ['pve-sbx-1', 'pve-sbx-2', 'pve-sbx-3'];
const PIS = ['adsb-office', 'streamer-basement-workbench', 'ups-compute-rack', 'ups-office'];
const CPU_LANE_MAX = 50;
const RUNTIME_NOTICE_MIN = 10;
const FRESH_SECONDS = 90;
const DEFAULT_STEP = 60_000;

/** WAN and monitoring findings have no Home tab. Without this they'd land on Kubernetes, the catch-all, and leave Network quiet. */
function systemOf(item: ReviewItem): SystemId {
  if (item.probe) {
    return PROBES[item.probe] ?? SERVICES_DEFAULT;
  }
  if (item.tab && TABS[item.tab]) {
    return TABS[item.tab];
  }
  if (item.labels?.instance?.startsWith('spark')) {
    return 'spark';
  }
  if (item.kind === 'wan') {
    return 'net';
  }
  return item.kind === 'monitor' || item.kind === 'probe' ? 'overview' : SERVICES_DEFAULT;
}

function alertSystem(labels: Record<string, string>): SystemId | 'lab' {
  if (labels.probe) {
    return PROBES[labels.probe] ?? SERVICES_DEFAULT;
  }
  if (labels.cluster === 'k8s-prod') {
    return 'k8s';
  }
  return labels.instance?.startsWith('spark') ? 'spark' : 'lab';
}

function firingSpans(series: Series): Span[] {
  const step = series.step ?? DEFAULT_STEP;
  const runs: Span[] = [];
  for (const [t, v] of series.points) {
    if (v !== 1) {
      continue;
    }
    const last = runs[runs.length - 1];
    if (last && t - last[1] <= step * 1.5) {
      last[1] = t;
    } else {
      runs.push([t - step, t]);
    }
  }
  return runs;
}

const mergeSpans = (list: Span[]) =>
  [...list].sort((a, b) => a[0] - b[0]).reduce<Span[]>((out, [a, b]) => {
    const last = out[out.length - 1];
    if (last && a <= last[1] + DEFAULT_STEP) {
      last[1] = Math.max(last[1], b);
    } else {
      out.push([a, b]);
    }
    return out;
  }, []);

function whenTrue(points: Point[], step: number, test: (value: number) => boolean): Span[] {
  const out: Span[] = [];
  for (const [t, v] of points) {
    if (v == null || !test(v)) {
      continue;
    }
    const last = out[out.length - 1];
    if (last && last[1] === t) {
      last[1] = t + step;
    } else {
      out.push([t, t + step]);
    }
  }
  return out;
}

const latest = (data: DataLike, ref: string, label: string) => new Map(seriesOf(data, ref).map((s) => [s.labels[label], instant(s)]));

function buildReview(review: Review, spansOf: (item: ReviewItem) => Span[], gaps: Gaps): ReviewModel {
  const rows = (items: ReviewItem[]) => items.map((item) => ({ ...item, system: systemOf(item), spans: spansOf(item) }));
  const action = rows(review.action);
  const short = gaps.missing + gaps.retained;
  if (short) {
    const parts = [gaps.missing && `${gaps.missing} missing`, gaps.retained && `${gaps.retained} retained`].filter(Boolean);
    action.push({ id: 'coverage/queries', label: 'Review queries not current', detail: `${parts.join(' · ')} of ${gaps.total}`, level: 2, unknown: true, kind: 'monitor', system: 'overview', spans: [] });
    action.sort((a, b) => b.level - a.level || Number(b.unknown) - Number(a.unknown) || a.label.localeCompare(b.label));
  }
  return { action, recovered: rows(review.recovered), observe: rows(review.observe), maintenance: rows(review.maintenance), confirmed: review.confirmed && !short };
}

/** The check roll-up: failed if any check failed, passing only if all ten passed, and a hole otherwise. */
function rollupOf(checks: Check[]): Band[] {
  const edges = [...new Set(checks.flatMap((c) => c.bands.flatMap((b) => [b[0], b[1]])))].sort((a, b) => a - b);
  const out: Band[] = [];
  for (let i = 0; i < edges.length - 1; i++) {
    const [a, z] = [edges[i], edges[i + 1]];
    const states = checks.map((c) => c.bands.find((b) => b[0] <= a && b[1] >= z)?.[2]);
    const value = states.includes(0) ? 0 : states.every((state) => state === 1) ? 1 : null;
    if (value == null) {
      continue;
    }
    const last = out[out.length - 1];
    if (last && last[2] === value && last[1] === a) {
      last[1] = z;
    } else {
      out.push([a, z, value]);
    }
  }
  return out;
}

export function buildHome(series: readonly FrameLike[], from: number, to: number, gaps: Gaps): HomeModel {
  const home = scoped(series, 'H_');
  const machines = scoped(series, 'M_');
  const ranges = measured(series);
  const probes = seriesOf(home, 'PH');
  const history = seriesOf(home, 'AH');

  const spansOf = (item: ReviewItem): Span[] => {
    if (item.kind === 'probe' && item.probe) {
      return dailyEpisodes(probes.find((s) => s.labels.probe === item.probe), from, to).map((e) => [e.start, e.end]);
    }
    const alert = history.find((s) => dailyIdentity(s.labels) === item.id);
    return alert ? firingSpans(alert) : [];
  };

  const daily = dailyReview(home, from, to);
  const review = home.series.length ? buildReview(daily, spansOf, gaps) : null;

  const http = latest(home, 'PC', 'probe');
  const durations = latest(home, 'PD', 'probe');
  const checks: Check[] = daily.checks.map((check) => ({
    ...check,
    system: PROBES[check.id] ?? SERVICES_DEFAULT,
    http: http.get(check.id) ?? null,
    ms: durations.get(check.id) ?? null,
    bands: dailyBands({ ...probes.find((s) => s.labels.probe === check.id), windowEnd: true }, from, to),
  }));

  const wanHistory = seriesOf(home, 'WNH');
  const wans = daily.wans.map((wan) => ({ ...wan, bands: dailyBands({ ...wanHistory.find((s) => s.labels.wan === wan.name), windowEnd: true }, from, to) }));

  const alerts = history.map((s) => ({ name: s.labels.alertname, severity: s.labels.severity, system: alertSystem(s.labels), spans: firingSpans(s) }));
  const alertSpans = (system: SystemId, test: (alert: (typeof alerts)[number]) => boolean) => mergeSpans(alerts.filter((a) => a.system === system && test(a)).flatMap((a) => a.spans));
  const failSpans = (system: SystemId) => checks.filter((c) => c.system === system).flatMap((c) => c.bands.filter((b) => b[2] === 0).map((b): Span => [b[0], b[1]]));
  const isMaintenance = (name: string) => name === 'NodeSecurityUpdatesPending';
  const marksOf = (system: SystemId, note: Span[] = []): Marks => ({
    crit: mergeSpans([...failSpans(system), ...alertSpans(system, (a) => a.severity === 'critical')]),
    warn: alertSpans(system, (a) => a.severity !== 'critical' && !isMaintenance(a.name)),
    maint: alertSpans(system, (a) => isMaintenance(a.name)),
    note,
  });

  const hosts = hostRows(machines);
  const cpu = (names: string[]) => names.map((name) => hosts.find((host) => host.instance === name)?.history ?? []);
  const points = (ref: string, label?: string, value?: string) => withGaps(seriesOf(ranges, ref).find((s) => !label || s.labels[label] === value));
  const stepOf = (list: Point[]) => smallestStep(list) ?? DEFAULT_STEP;

  const read = points('R_nas_read');
  const written = points('R_nas_written');
  const disk = written.length ? combine([read, written], ([a, b]) => (a + b) / 1e6) : scale(read, (v) => v / 1e6);
  const received = seriesOf(ranges, 'R_net_wanRx').map((s) => withGaps(s));
  const sent = seriesOf(ranges, 'R_net_wanTx').map((s) => withGaps(s));
  const wan = combine([...received, ...sent], (row) => (row.reduce((sum, v) => sum + v, 0) * 8) / 1e6);
  const runtime = points('R_ups_runtimeMin', 'ups', 'compute-rack');
  const pve = combine(cpu(PVE_HOSTS), (row) => row.reduce((sum, v) => sum + v, 0) / row.length);
  const busiest = combine(cpu(PIS), (row) => Math.max(...row));
  const failedDsm = review?.recovered.some((item) => item.id === 'probe/athena-dsm');

  const lane = (system: SystemId, color: Accent, metric: string, unit: string, ref: string, list: Point[], decimals: number, extra: Partial<Lane> = {}): Lane => ({
    system, color, metric, unit, ref, points: list, step: stepOf(list), decimals, marks: marksOf(system), ...extra,
  });

  const lanes = [
    lane('k8s', 'azure', 'Service requests', 'req/s', 'R_k8s_requests', points('R_k8s_requests'), 1),
    lane('pve', 'sea', `Host CPU, mean of ${PVE_HOSTS.length}`, '%', 'M_E', pve, 1, { max: CPU_LANE_MAX }),
    lane('spark', 'orchid', 'Inference output', 'tok/s', 'R_spark_tokens', points('R_spark_tokens'), 1),
    lane('nas', 'azure', written.length ? 'Disk read + write' : 'Disk read', 'MB/s', 'R_nas_read', disk, 0, failedDsm ? { label: 'DSM check failed' } : {}),
    lane('net', 'sea', sent.length ? 'WAN traffic, both ports' : 'WAN receive, both ports', 'Mbit/s', 'R_net_wanRx', wan, 1),
    lane('power', 'orchid', 'compute-rack UPS load', '% of capacity', 'R_ups_load', points('R_ups_load', 'ups', 'compute-rack'), 0, {
      max: 100,
      marks: marksOf('power', whenTrue(runtime, stepOf(runtime), (v) => v < RUNTIME_NOTICE_MIN)),
    }),
    lane('dev', 'azure', 'Busiest Pi CPU', '%', 'M_E', busiest, 1, { max: CPU_LANE_MAX }),
  ];

  const value = (ref: string) => scalar(home, ref);
  const ruleAge = value('MA');
  const monitor: Monitor = {
    rulesEvaluating: value('MV') === 1 && ruleAge != null && ruleAge <= FRESH_SECONDS,
    down: value('MD'),
    notifyFailures: value('MN'),
    heartbeat: value('MH'),
  };

  return { review, checks, wans, hosts, monitor, lanes, rollup: rollupOf(checks), notices: runtime.length > 0 };
}

/** The last sample, but only if it reaches the window's end. A series that stopped earlier has no value at window end. */
export function valueAtEnd(lane: Lane, to: number) {
  for (let i = lane.points.length - 1; i >= 0; i--) {
    const [t, v] = lane.points[i];
    if (v != null) {
      return to - t <= lane.step * 1.5 ? v : null;
    }
  }
  return null;
}

/** The sample at a moment, or null inside a gap: a cursor never borrows a neighbouring value. */
export function sampleAt(lane: Lane, t: number) {
  let best: Point | null = null;
  for (const point of lane.points) {
    if (!best || Math.abs(point[0] - t) < Math.abs(best[0] - t)) {
      best = point;
    }
  }
  return best && Math.abs(best[0] - t) <= lane.step ? best[1] : null;
}

export function cpuScaleTop(hosts: HostRow[]) {
  const peak = Math.max(0, ...hosts.flatMap((host) => host.history.flatMap(([, v]) => (v == null ? [] : [v]))));
  return Math.max(10, Math.ceil(peak / 10) * 10);
}
