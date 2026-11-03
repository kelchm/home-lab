import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const source = fs.readFileSync(new URL('../../kubernetes/apps/observability/grafana/app/aurora/module.js', import.meta.url), 'utf8');
function importModule(f) {
  let result;
  vm.runInNewContext(source, {
    define(dependencies, factory) { result = factory({ ...f.data, AppPlugin: class {} }, f.runtime); },
    setTimeout: f.options.schedule,
    clearTimeout: f.options.cancel,
    console: { warn: f.options.warn },
  });
  return result;
}
function load(f) {
  const result = importModule(f);
  result.plugin.init({ enabled: true });
  return result.aurora;
}
function fixture({ version = '13.3.0', initial = 'Dark', providerDelay = 0, throws = false, provider = true } = {}) {
  let now = 0, id = 0, current = initial ? { name: initial } : undefined, builds = 0;
  const queue = new Map(), listeners = new Set(), requests = [], warnings = [];
  const schedule = (fn, ms) => { queue.set(++id, { fn, at: now + ms }); return id; };
  const tick = (until) => { while (queue.size) { const [key, item] = [...queue].sort((a, b) => a[1].at - b[1].at)[0]; if (item.at > until) break; queue.delete(key); now = item.at; item.fn(); } now = until; };
  class ThemeChangedEvent { constructor(payload) { this.payload = payload; } }
  const events = { publish(event) { requests.push(event.payload.name); for (const fn of listeners) fn(event); if (provider) schedule(() => { current = event.payload; }, providerDelay); }, subscribe(type, fn) { listeners.add(fn); return { unsubscribe() { listeners.delete(fn); } }; } };
  const config = { buildInfo: { version } };
  Object.defineProperty(config, 'theme2', { get: () => current, set() { throw new Error('Adapter must never write global theme state'); } });
  const runtime = { config, ThemeChangedEvent, getAppEvents: () => events };
  const data = { createTheme() { builds++; if (throws) throw new Error('incompatible schema'); return { name: 'Homelab Aurora', isDark: true }; } };
  const options = { schedule, cancel: (key) => queue.delete(key), warn: (value) => warnings.push(value) };
  return { runtime, data, options, tick, queue, requests, warnings, current: () => current?.name, builds: () => builds, select: (name) => events.publish(new ThemeChangedEvent({ name })), setInitial: (name) => { current = { name }; } };
}
test('the provider owns state; an event applies Aurora without any global assignment', () => {
  const f = fixture(); const c = load(f); f.tick(4000);
  assert.equal(f.current(), 'Homelab Aurora'); assert.equal(c.state, 'applied'); assert.equal(f.queue.size, 0);
});
test('module import and disabled or unknown initialization are inert', () => {
  const f = fixture(); const result = importModule(f);
  result.plugin.init({ enabled: false }); result.plugin.init({}); f.tick(4000);
  assert.equal(result.aurora, undefined); assert.equal(f.builds(), 0);
  assert.equal(f.queue.size, 0); assert.deepEqual(f.requests, []);
});
test('repeated enabled initialization installs only once', () => {
  const f = fixture(); const result = importModule(f);
  result.plugin.init({ enabled: false }); result.plugin.init({ enabled: true });
  const controller = result.aurora;
  result.plugin.init({ enabled: true }); f.tick(4000);
  assert.equal(result.aurora, controller); assert.equal(f.builds(), 1);
  assert.equal(f.requests.length, 1); assert.equal(f.current(), 'Homelab Aurora');
});
test('missed startup events are retried, within a fixed window', () => {
  const f = fixture({ provider: false }); const c = load(f); f.tick(4000);
  assert.equal(f.current(), 'Dark'); assert.equal(c.state, 'native fallback'); assert.equal(f.requests.length, 4); assert.equal(f.queue.size, 0); assert.equal(f.warnings.length, 1);
});
test('a new version with compatible APIs applies Aurora without an allow-list edit', () => {
  const f = fixture({ version: '14.0.0' }); load(f); f.tick(4000);
  assert.equal(f.builds(), 1); assert.equal(f.current(), 'Homelab Aurora'); assert.equal(f.queue.size, 0);
});
test('a new version with an incompatible factory retains native styling', () => {
  const f = fixture({ version: '14.0.0', throws: true }); load(f); f.tick(4000);
  assert.equal(f.builds(), 1); assert.deepEqual(f.requests, []); assert.equal(f.current(), 'Dark'); assert.equal(f.queue.size, 0);
});
test('factory failure leaves native styling and no timers or requests', () => {
  const f = fixture({ throws: true }); load(f); f.tick(4000);
  assert.equal(f.current(), 'Dark'); assert.deepEqual(f.requests, []); assert.equal(f.queue.size, 0);
});
test('a missing internal factory leaves native styling', () => {
  const f = fixture(); delete f.data.createTheme; load(f); assert.deepEqual(f.requests, []);
});
for (const name of ['Light', 'Tron']) test(`${name} remains the selected appearance`, () => {
  const f = fixture({ initial: name }); load(f); f.tick(4000);
  assert.equal(f.current(), name); assert.deepEqual(f.requests, []);
});
test('a slow native switch back to Dark eventually requests Aurora', () => {
  const f = fixture({ initial: 'Light', providerDelay: 800 }); load(f); f.tick(4000);
  f.select('Dark'); f.tick(8000); assert.equal(f.current(), 'Homelab Aurora');
});
test('an existing Aurora value cannot mask a slow native Dark selection', () => {
  const f = fixture({ initial: 'Homelab Aurora', providerDelay: 800 }); load(f); f.tick(4000);
  f.select('Dark'); f.tick(8000); assert.equal(f.current(), 'Homelab Aurora');
});
test('a later Light choice cancels pending Dark reapplication', () => {
  const f = fixture({ initial: 'Light', providerDelay: 800 }); load(f); f.tick(4000);
  f.select('Dark'); f.tick(4300); f.select('Light'); f.tick(8000); assert.equal(f.current(), 'Light');
});
test('missing initial state is not mistaken for a chosen native theme', () => {
  const f = fixture({ initial: null }); load(f); f.tick(800); f.setInitial('Dark'); f.tick(4000); assert.equal(f.current(), 'Homelab Aurora');
});
test('stop removes the subscription and cancels all pending work', () => {
  const f = fixture(); const c = load(f); c.stop(); f.select('Dark'); f.tick(4000);
  assert.equal(f.current(), 'Dark'); assert.deepEqual(f.requests, ['Dark']); assert.equal(f.queue.size, 0);
});
