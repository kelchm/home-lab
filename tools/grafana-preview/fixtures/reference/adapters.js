// ---- Data adapters ------------------------------------------------------------------
// Pure functions from Grafana's PanelData to plain rows. Nothing here touches the DOM or
// Grafana, so test/adapters.test.mjs runs this exact file against saved query frames.

// Values are plain arrays today; older Grafana hands panels a Vector with .get(i).
const fieldValues = (field) => {
  const v = field?.values;
  if (v == null) return [];
  return typeof v.get === 'function' && !Array.isArray(v) ? Array.from({ length: v.length }, (_, i) => v.get(i)) : Array.from(v);
};

const toNumber = (v) => (v == null || v === '' || !Number.isFinite(Number(v)) ? null : Number(v));

// One entry per numeric field of every frame with this refId; several frames may share it.
const seriesOf = (data, refId) => {
  const out = [];
  for (const frame of data?.series ?? []) {
    if (frame.refId !== refId) continue;
    const timeField = (frame.fields ?? []).find((f) => f.type === 'time');
    const times = fieldValues(timeField);
    for (const field of frame.fields ?? []) {
      if (field.type !== 'number') continue;
      out.push({
        labels: field.labels ?? {},
        points: fieldValues(field).map((v, i) => [times[i] ?? null, toNumber(v)]),
        step: toNumber(timeField?.config?.interval),
      });
    }
  }
  return out;
};

// An instant query's value, or null when it returned nothing. Never reads a range query.
const instant = (series) => series?.points[series.points.length - 1]?.[1] ?? null;
const scalar = (data, refId) => instant(seriesOf(data, refId)[0]);

// The smallest interval seen: holes can outnumber neighbours in a short series, so a median can't be trusted.
const smallestStep = (points) => {
  const steps = points.slice(1).map((p, i) => p[0] - points[i][0]).filter((d) => d > 0);
  return steps.length ? Math.min(...steps) : null;
};

// Prometheus omits timestamps with no sample, so a missing step becomes an explicit null.
// Charts then break the line there instead of joining across the hole.
const withGaps = (series) => {
  const points = (series?.points ?? []).filter((p) => p[0] != null).sort((a, b) => a[0] - b[0]);
  const step = series?.step ?? smallestStep(points);
  const out = [];
  points.forEach((p, i) => {
    if (i && step && p[0] - points[i - 1][0] > step * 1.5) out.push([points[i - 1][0] + step, null]);
    out.push(p);
  });
  return out;
};

// Runs of consecutive samples. A run of one has no line to draw, so it gets a dot.
const segments = (points) => {
  const runs = [];
  let run = null;
  for (const p of points) {
    if (p[1] == null) run = null;
    else if (run) run.push(p);
    else runs.push((run = [p]));
  }
  return runs;
};

const extent = (points) => {
  const ys = points.map((p) => p[1]).filter((v) => v != null);
  return ys.length ? { min: Math.min(...ys), max: Math.max(...ys) } : null;
};

// The sample the selected range opens on, or null when the series starts later than that.
const rangeStart = (points, from) => {
  const step = smallestStep(points) ?? 0;
  const first = points.find((p) => p[1] != null);
  return first && first[0] <= from + step * 1.5 ? first[1] : null;
};

// ---- Machines: identity is instance + platform -----------------------------------------
const HOST_STATES = {
  collecting: { label: 'Collecting', attention: false },
  failing: { label: 'Scrape failing', attention: true },
  missing: { label: 'No current data', attention: true },
};
const hostState = (up) => (up == null ? 'missing' : up >= 1 ? 'collecting' : 'failing');

const PLATFORMS = ['k8s', 'pve', 'spark', 'synology', 'pi'];
const platformRank = (p) => (PLATFORMS.includes(p) ? PLATFORMS.indexOf(p) : PLATFORMS.length);

const hostRows = (data) => {
  const hosts = new Map();
  const host = ({ labels: { instance, platform } }) => {
    const key = `${platform}/${instance}`;
    if (!hosts.has(key)) hosts.set(key, { instance, platform, cpu: null, memory: null, up: null, age: null, history: [] });
    return hosts.get(key);
  };
  // Every query contributes rows, so a machine that one query loses stays listed.
  seriesOf(data, 'A').forEach(host);
  seriesOf(data, 'B').forEach((s) => { host(s).cpu = instant(s); });
  seriesOf(data, 'C').forEach((s) => { host(s).memory = instant(s); });
  seriesOf(data, 'D').forEach((s) => { host(s).up = instant(s); });
  seriesOf(data, 'F').forEach((s) => { host(s).age = instant(s); });
  seriesOf(data, 'E').forEach((s) => { host(s).history = withGaps(s); });
  const rows = [...hosts.values()].map((h) => ({ ...h, state: hostState(h.up) }));
  return rows.sort((a, b) => HOST_STATES[b.state].attention - HOST_STATES[a.state].attention
    || platformRank(a.platform) - platformRank(b.platform)
    || a.platform.localeCompare(b.platform)
    || a.instance.localeCompare(b.instance, undefined, { numeric: true }));
};

// ---- Storage: identity is node ----------------------------------------------------------
const capacityRows = (data) => {
  const nodes = new Map();
  const node = (name) => {
    if (!nodes.has(name)) nodes.set(name, { node: name, used: null, capacity: null });
    return nodes.get(name);
  };
  seriesOf(data, 'A').forEach((s) => { node(s.labels.node).used = instant(s); });
  seriesOf(data, 'B').forEach((s) => { node(s.labels.node).capacity = instant(s); });
  return [...nodes.values()]
    .map((n) => ({ ...n, percent: n.used != null && n.capacity ? (100 * n.used) / n.capacity : null }))
    .sort((a, b) => a.node.localeCompare(b.node, undefined, { numeric: true }));
};

// ---- Service checks -----------------------------------------------------------------------
const checkState = (passing, total) => {
  if (passing == null) return { attention: true, label: 'No current data' };
  if (total == null) return { attention: true, label: 'Total unknown' };
  if (passing < total) return { attention: true, label: `${total - passing} failing` };
  return { attention: false, label: 'All passing' };
};

// ---- Formatting ------------------------------------------------------------------------------
const formatNumber = (v, decimals = 0) => (v == null ? '–' : v.toLocaleString('en-US', { minimumFractionDigits: decimals, maximumFractionDigits: decimals }));

const formatBytes = (bytes) => {
  if (bytes == null) return '–';
  const tib = bytes / 1024 ** 4;
  return tib >= 1 ? `${formatNumber(tib, 2)} TiB` : `${formatNumber(bytes / 1024 ** 3, 0)} GiB`;
};

const formatAge = (seconds) => {
  if (seconds == null) return '–';
  if (seconds < 90) return `${Math.max(0, Math.round(seconds))} s ago`;
  if (seconds < 5400) return `${Math.round(seconds / 60)} min ago`;
  return `${Math.round(seconds / 3600)} h ago`;
};

const formatSpan = (ms) => {
  const hours = ms / 3_600_000;
  if (hours < 1) return `${Math.round(ms / 60_000)} min`;
  if (hours < 48) return `${formatNumber(hours, Number.isInteger(Math.round(hours * 10) / 10) ? 0 : 1)} h`;
  return `${Math.round(hours / 24)} d`;
};

// A change in percentage points, with a word for the case that rounds away to nothing.
const formatDelta = (delta, decimals = 1) => {
  if (delta == null) return null;
  const rounded = Number(delta.toFixed(decimals));
  if (rounded === 0) return 'No change';
  return `${rounded > 0 ? '▲' : '▼'} ${formatNumber(Math.abs(rounded), decimals)} pt`;
};

const hostsUrl = (base, host, timeQuery) => {
  const query = new URLSearchParams({ 'var-platform': host.platform, 'var-node': host.instance });
  return `${base}/d/hosts?${query}${timeQuery ? `&${timeQuery}` : ''}`;
};
