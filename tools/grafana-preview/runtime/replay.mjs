import { createHash } from 'node:crypto';

export const expressionHash = (expr) => createHash('sha256').update(expr).digest('hex').slice(0, 16);
// Tokenize outside quoted strings: whitespace is irrelevant, label values and token boundaries are not.
export const canonical = (expr) => (expr.match(/"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`[^`]*`|\$__\w+|[\w.:]+|!=|==|=~|!~|>=|<=|[^\s]/g) ?? []).join(' ');
const escape = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
export const expressionPattern = (expr) => new RegExp('^' + canonical(expr).split(/(\$__\w+)/).map((token) => token.startsWith('$__') ? '[\\w.]+' : escape(token)).join('') + '$');

export function clipSeries(series, window) {
  return series.map((s) => {
    const points = s.t.map((t, i) => [t, s.v[i]]).filter(([t]) => Number.isFinite(t) && t >= window.from && t <= window.to);
    return { ...s, labels: { ...s.labels }, t: points.map(([t]) => t), v: points.map(([, v]) => v) };
  }).filter((s) => s.t.length);
}

export function mergeSeries(groups) {
  const merged = new Map();
  for (const series of groups.flat()) {
    const key = JSON.stringify(Object.entries(series.labels).sort(([a], [b]) => a.localeCompare(b)));
    const target = merged.get(key) ?? { labels: series.labels, step: series.step, points: new Map() };
    series.t.forEach((t, i) => {
      if (target.points.has(t) && target.points.get(t) !== series.v[i]) throw new Error(`Conflicting capture at ${t} for ${key}`);
      target.points.set(t, series.v[i]);
    });
    merged.set(key, target);
  }
  return [...merged.values()].map(({ labels, step, points }) => {
    const sorted = [...points].sort(([a], [b]) => a - b);
    return { labels, ...(step == null ? {} : { step }), t: sorted.map(([t]) => t), v: sorted.map(([, v]) => v) };
  });
}

/** Grafana puts executedQueryString on the first frame of a result. It describes ALL frames of that result. */
export function inferenceCaptures(responses) {
  const captures = new Map();
  for (const [responseIndex, response] of responses.entries()) {
    for (const [ref, result] of Object.entries(response.results ?? {})) {
      const frames = result.frames ?? [];
      const query = response.request?.queries?.find((q) => q.refId === ref);
      const metadata = result.meta?.executedQueryString ?? frames.find((f) => f.schema?.meta?.executedQueryString)?.schema.meta.executedQueryString;
      const executedExpr = metadata?.match(/^Expr: ([\s\S]*?)(?:\nStep:|$)/)?.[1] ?? query?.expr;
      if (!executedExpr) continue;
      const key = `expr:${expressionHash(executedExpr)}`;
      const range = query?.range ?? frames.some((f) => f.schema?.meta?.custom?.resultType === 'matrix');
      const window = { from: Number(response.request?.from), to: Number(response.request?.to) };
      if (!Number.isFinite(window.from) || !Number.isFinite(window.to)) throw new Error(`Inference response ${responseIndex}: missing dated request window`);
      const series = frames.flatMap((frame) => {
        const fields = frame.schema?.fields ?? [], values = frame.data?.values ?? [];
        const timeIndex = fields.findIndex((f) => f.type === 'time');
        if (timeIndex < 0) return [];
        return fields.flatMap((field, i) => field.type !== 'number' ? [] : [{ labels: field.labels ?? {}, t: values[timeIndex] ?? [], v: values[i] ?? [], step: fields[timeIndex].config?.interval }]);
      });
      const mapKey = `${key}:${Boolean(range)}`;
      const previous = captures.get(mapKey);
      const evidence = { file: 'fixtures/inference.json', responseIndex, ref, executedExpr, executedExprHash: expressionHash(executedExpr), requestExpr: query?.expr ?? null, window };
      const error = result.error ?? (result.status >= 400 ? `Captured HTTP ${result.status}` : null);
      const segments = [...(previous?.segments ?? []), { window, series: error ? [] : clipSeries(series, window), ...(error ? { error } : {}) }];
      captures.set(mapKey, {
        expr: executedExpr, range: Boolean(range),
        window: { from: Math.min(...segments.map((s) => s.window.from)), to: Math.max(...segments.map((s) => s.window.to)) },
        series: mergeSeries(segments.map((s) => s.series)), segments,
        provenance: [...(previous?.provenance ?? []), { ...evidence, ...(error ? { error } : {}) }],
        error: segments.every((s) => s.error) ? error : null,
      });
    }
  }
  return captures;
}

export function assembleCatalog({ base, commonWindow, bounded, inference, domains, common = [] }) {
  const inferred = inferenceCaptures(inference);
  const commonByRef = new Map(common.map((query) => [query.refId, query]));
  if (commonByRef.size !== common.length || common.some((query) => !base.some((entry) => entry.refId === query.refId))) throw new Error('Invalid common query override');
  const definitions = [...base.map(({ series, ...query }) => commonByRef.get(query.refId) ?? query), ...domains];
  const seen = new Set();
  for (const q of definitions) {
    if (seen.has(q.refId)) throw new Error(`Duplicate query reference ${q.refId}`);
    seen.add(q.refId);
    if (!q.expr?.trim() || q.instant === q.range) throw new Error(`Invalid query ${q.refId}`);
  }
  const entries = definitions.map((q) => {
    let capture;
    const locator = q.capture ?? { kind: 'catalog', key: q.refId };
    if (locator.kind === 'catalog') {
      const e = base.find((e) => e.refId === locator.key && e.range === q.range);
      if (e) capture = { ...e, window: commonWindow, provenance: [{ file: 'runtime/catalog.json', ref: e.refId, executedExpr: e.expr, executedExprHash: expressionHash(e.expr), window: commonWindow }] };
    } else if (locator.kind === 'bounded') {
      const kind = (locator.range ?? q.range) ? 'range' : 'instant';
      const e = bounded[kind]?.[locator.key];
      if (e && (kind === 'range') === q.range) {
        const window = { from: bounded.from, to: bounded.to };
        capture = { ...e, window, provenance: [{ file: 'fixtures/bounded.json', key: locator.key, kind, fetchedAt: bounded.fetchedAt, executedExpr: e.expr, executedExprHash: expressionHash(e.expr), window }] };
      }
    } else if (locator.kind === 'inference') capture = inferred.get(`${locator.key}:${q.range}`);
    const segments = !capture ? [] : capture.segments ?? [{ window: capture.window, series: capture.error ? [] : clipSeries(capture.series ?? [], capture.window), ...(capture.error ? { error: capture.error } : {}) }];
    const series = mergeSeries(segments.map((segment) => segment.series));
    return {
      ...q, aliases: [q.refId], expressions: [...new Set([q.expr, capture?.expr].filter(Boolean))], series, window: capture?.window ?? null,
      segments,
      captureStatus: !capture ? 'missing' : capture.error ? 'captured-error' : series.length ? 'captured' : 'captured-empty',
      provenance: (capture?.provenance ?? []).map((e) => ({ ...e, aliasDiffers: canonical(q.expr) !== canonical(e.executedExpr), locator })),
      ...(capture?.error ? { captureError: capture.error } : {}),
    };
  });
  // Equal expressions are one Prometheus request even if Grafana gives them different refIds.
  const groups = new Map();
  for (const e of entries) {
    const key = `${e.range}:${canonical(e.expr)}`, old = groups.get(key);
    if (!old) { groups.set(key, e); continue; }
    old.aliases.push(e.refId); old.expressions = [...new Set([...old.expressions, ...e.expressions])];
    old.series = mergeSeries([old.series, e.series]); old.provenance.push(...e.provenance); old.segments.push(...e.segments);
    old.captureStatus = old.series.length ? 'captured' : old.captureStatus === 'missing' ? e.captureStatus : old.captureStatus;
    if (e.window) old.window = old.window ? { from: Math.min(old.window.from, e.window.from), to: Math.max(old.window.to, e.window.to) } : e.window;
  }
  return [...groups.values()];
}

export const compileCatalog = (catalog) => catalog.map((entry) => ({ ...entry, patterns: (entry.expressions ?? [entry.expr]).map(expressionPattern) }));
export const findEntry = (catalog, expr, range) => {
  const normalized = canonical(expr);
  return catalog.find((e) => e.range === range && canonical(e.expr) === normalized) ?? catalog.find((e) => e.range === range && e.patterns.some((p) => p.test(normalized)));
};
export const matchesRef = (entry, pattern) => Boolean(pattern && entry && (entry.aliases ?? [entry.refId]).some((ref) => new RegExp(pattern).test(ref)));

export function replay(entry, { range, start, end, time, state = {} }) {
  if (!entry || matchesRef(entry, state.emptyRefs)) return [];
  // Never turn old captured samples into current facts.
  if (!range && entry.window && (time < entry.window.from || time > entry.window.to)) return [];
  // Gap injection is local to each dated capture, including aliases with widely separated windows.
  const segments = entry.segments ?? [{ window: entry.window, series: entry.series }];
  const series = mergeSeries(segments.filter((segment) => !segment.error && (range || !segment.window || time >= segment.window.from && time <= segment.window.to)).map((segment) => {
    const gap = state.gapWindow ?? (segment.window && { from: segment.window.from + (segment.window.to - segment.window.from) * .4, to: segment.window.from + (segment.window.to - segment.window.from) * .6 });
    return segment.series.map((s) => {
      const points = s.t.map((t, i) => [t, s.v[i]]).filter(([t]) => !matchesRef(entry, state.gapRefs) || !gap || t < gap.from || t > gap.to);
      return { ...s, t: points.map(([t]) => t), v: points.map(([, v]) => v) };
    });
  }));
  return series.filter((s) => !state.missingEntity || !Object.values(s.labels).includes(state.missingEntity)).map((s) => {
    const points = s.t.map((t, i) => [t, s.v[i]])
      .filter(([t, v]) => v != null && Number.isFinite(v) && (range ? t >= start && t <= end : t <= time));
    if (!points.length) return null;
    return range ? { metric: s.labels, values: points.map(([t, v]) => [t / 1000, String(v)]) } : { metric: s.labels, value: [points.at(-1)[0] / 1000, String(points.at(-1)[1])] };
  }).filter(Boolean);
}
