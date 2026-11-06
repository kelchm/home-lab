// Isolated, dated Prometheus replay. This process has no live upstream or credentials.
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { randomUUID } from 'node:crypto';
import { compileCatalog, findEntry, matchesRef, replay } from './replay.mjs';

export function createReplayServer({ catalog, stateDir, logDir = stateDir, lane = 'local' }) {
  fs.mkdirSync(stateDir, { recursive: true }); fs.mkdirSync(logDir, { recursive: true });
  const compiled = compileCatalog(catalog);
  const stateFile = path.join(stateDir, 'state.json'), logFile = path.join(logDir, 'requests.jsonl');
  const state = () => { try { return JSON.parse(fs.readFileSync(stateFile, 'utf8')); } catch { return {}; } };
  const log = (event) => fs.appendFileSync(logFile, JSON.stringify({ lane, at: Date.now(), ...event }) + '\n');
  const send = (res, status, body) => { res.writeHead(status, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)); };
  const ok = (res, data) => send(res, 200, { status: 'success', data });
  return http.createServer(async (req, res) => {
    try {
      const url = new URL(req.url, 'http://fixture');
      let body = ''; for await (const chunk of req) { body += chunk; if (body.length > 1048576) return send(res, 413, { error: 'Request too large' }); }
      if (url.pathname === '/health') return send(res, 200, { ok: true, lane, entries: catalog.length });
      if (url.pathname === '/control' && req.method === 'GET') return send(res, 200, state());
      if (url.pathname === '/control' && req.method === 'POST') {
        const next = JSON.parse(body);
        if (!next || Array.isArray(next) || typeof next !== 'object') throw new Error('Control must be an object');
        for (const key of ['failRefs', 'emptyRefs', 'gapRefs', 'delayRefs']) if (next[key] != null) new RegExp(next[key]);
        if (next.delayMs != null && (!Number.isFinite(next.delayMs) || next.delayMs < 0 || next.delayMs > 60000)) throw new Error('delayMs must be 0–60000');
        fs.writeFileSync(`${stateFile}.tmp`, JSON.stringify(next)); fs.renameSync(`${stateFile}.tmp`, stateFile);
        log({ phase: 'control', state: next }); return send(res, 200, { ok: true, lane });
      }
      if (url.pathname === '/logs') {
        const events = fs.existsSync(logFile) ? fs.readFileSync(logFile, 'utf8').trim().split('\n').filter(Boolean).map(JSON.parse) : [];
        return send(res, 200, events.filter((e) => (!url.searchParams.has('since') || e.at >= Number(url.searchParams.get('since'))) && (!url.searchParams.has('ref') || e.aliases?.includes(url.searchParams.get('ref'))) && (!url.searchParams.has('phase') || e.phase === url.searchParams.get('phase'))).slice(-1000));
      }
      if (url.pathname.endsWith('/status/buildinfo')) return ok(res, { version: '2.55.0', application: 'dated-observability-replay' });
      const p = new URLSearchParams(url.search); new URLSearchParams(body).forEach((v, k) => p.set(k, v));
      if (url.pathname.endsWith('/metadata')) return ok(res, {});
      const range = url.pathname.endsWith('/query_range');
      if (!range && !url.pathname.endsWith('/query')) {
        const label = url.pathname.match(/\/label\/([^/]+)\/values$/)?.[1];
        const all = compiled.flatMap((e) => e.series);
        // An unimplemented matcher must not return inventory from other systems.
        if (p.has('match[]')) return ok(res, []);
        if (label) return ok(res, [...new Set(all.map((s) => s.labels[decodeURIComponent(label)]).filter((v) => v != null))].sort());
        if (url.pathname.endsWith('/labels')) return ok(res, [...new Set(all.flatMap((s) => Object.keys(s.labels)))].sort());
        return ok(res, []);
      }
      const expr = p.get('query') ?? '', entry = findEntry(compiled, expr, range), s = state();
      const event = { rid: randomUUID(), ref: entry?.refId ?? null, aliases: entry?.aliases ?? [], range, expr, captureStatus: entry?.captureStatus ?? 'unmatched', start: p.get('start'), end: p.get('end'), time: p.get('time') };
      log({ ...event, phase: 'start' });
      let cancelled = false, timer;
      res.on('close', () => { if (!res.writableEnded) { cancelled = true; clearTimeout(timer); log({ ...event, phase: 'cancelled' }); } });
      if (s.delayMs && (!s.delayRefs || matchesRef(entry, s.delayRefs))) {
        await new Promise((resolve) => { timer = setTimeout(resolve, s.delayMs); res.once('close', resolve); });
      }
      if (cancelled) return;
      if (matchesRef(entry, s.failRefs)) {
        log({ ...event, phase: 'error' });
        return send(res, s.status >= 400 && s.status <= 599 ? s.status : 503, { status: 'error', errorType: 'unavailable', error: `Injected preview query failure (${entry.aliases.join(', ')})` });
      }
      const result = replay(entry, { range, start: Number(p.get('start')) * 1000, end: Number(p.get('end')) * 1000, time: p.has('time') ? Number(p.get('time')) * 1000 : Date.now(), state: s });
      log({ ...event, phase: 'done', series: result.length });
      return ok(res, { resultType: range ? 'matrix' : 'vector', result });
    } catch (error) { send(res, 400, { status: 'error', errorType: 'bad_data', error: error.message }); }
  });
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const dir = path.dirname(fileURLToPath(import.meta.url));
  const server = createReplayServer({ catalog: JSON.parse(fs.readFileSync(process.env.CATALOG ?? path.join(dir, 'catalog.json'), 'utf8')), stateDir: process.env.STATE_DIR ?? '/state', logDir: process.env.LOG_DIR ?? '/logs', lane: process.env.LANE ?? 'local' });
  server.listen(Number(process.env.PORT ?? 9090), '0.0.0.0');
}
