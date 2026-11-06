import { describe, expect, test } from 'vitest';
import { FOCUSES } from '../src/types';
import { machineTarget } from '../src/views/machine-links';
import { range } from './fixtures';

const links = Object.fromEntries(FOCUSES.map((focus) => [focus, `/a/homelab-observability-app/explore?focus=${focus}&source=live&from=100&to=200&timezone=UTC`]));
const ctx = { links, mode: 'live' as const, timeRange: range(100, 200) };

describe('Machines investigation links', () => {
  test.each([['spark', 'Sparks', 'host'], ['pve', 'Proxmox', 'node'], ['pi', 'Devices', 'host']])('%s opens its system detail in the same context', (platform, focus, view) => {
    const target = machineTarget({ platform, instance: 'a host / + &' }, ctx);
    const params = new URL(target.href, 'https://preview.invalid').searchParams;
    expect(target.outside).toBe(false);
    expect(params.get('focus')).toBe(focus);
    expect(params.get('view')).toBe(view);
    expect(params.get('entity')).toBe('a host / + &');
    expect(params.get('source')).toBe('live');
    expect(params.get('from')).toBe('100');
    expect(params.get('timezone')).toBe('UTC');
  });

  test('other platforms use the local Hosts copy with the selected datasource', () => {
    const target = machineTarget({ platform: 'k8s', instance: 'k8s-prod-1' }, ctx);
    expect(target.outside).toBe(false);
    const url = new URL(target.href, 'https://preview.invalid');
    expect(url.pathname).toBe('/d/hosts');
    expect(url.searchParams.get('var-datasource')).toBe('comparison-live');
    expect(url.searchParams.get('var-node')).toBe('k8s-prod-1');
    expect(url.searchParams.get('var-platform')).toBe('k8s');
  });

  test('a standalone panel without internal navigation preserves its explicit external fallback', () => {
    const target = machineTarget({ platform: 'spark', instance: 'spark-1' }, { ...ctx, links: {} });
    expect(target.outside).toBe(true);
    expect(target.href).toMatch(/^https:\/\/grafana.home.kelch.io\/d\/hosts/);
  });
});
