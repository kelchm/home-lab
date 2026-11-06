import { GRAFANA } from '../model/systems';
import { hostsUrl, type HostRow } from '../model/semantics';
import { FOCUSES } from '../types';
import { timeQuery, type Target, type ViewContext } from './context';

type Context = Pick<ViewContext, 'links' | 'mode' | 'timeRange'>;

export function machineTarget(host: Pick<HostRow, 'instance' | 'platform'>, ctx: Context): Target {
  const destination = ({ spark: ['Sparks', 'host'], pve: ['Proxmox', 'node'], pi: ['Devices', 'host'] } as Record<string, [string, string]>)[host.platform];
  if (destination && ctx.links[destination[0]]) {
    const original = ctx.links[destination[0]];
    const url = new URL(original, 'https://preview.invalid');
    url.searchParams.set('view', destination[1]);
    url.searchParams.set('entity', host.instance);
    return { href: /^https?:\/\//.test(original) ? url.href : `${url.pathname}${url.search}${url.hash}`, outside: false };
  }
  if (FOCUSES.every((focus) => Boolean(ctx.links[focus])) && ctx.mode !== 'unknown') {
    const overview = new URL(ctx.links.Overview, 'https://preview.invalid');
    const params = new URLSearchParams({
      from: String(ctx.timeRange.raw.from), to: String(ctx.timeRange.raw.to),
      'var-platform': host.platform, 'var-node': host.instance,
      'var-datasource': ctx.mode === 'live' ? 'comparison-live' : 'comparison',
    });
    const zone = overview.searchParams.get('timezone');
    if (zone) params.set('timezone', zone);
    return { href: `/d/hosts?${params}`, outside: false };
  }
  return { href: hostsUrl(GRAFANA, host as HostRow, timeQuery(ctx.timeRange)), outside: true };
}
