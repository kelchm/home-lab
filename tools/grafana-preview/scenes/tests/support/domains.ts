// Explicitly synthetic domains isolate shell/lifecycle behavior from each domain owner's telemetry semantics.
import type { DomainDefinition } from '@shared/domains/contract';
import type { Focus } from '@shared/types';
const systems = [['k8s', 'Kubernetes'], ['pve', 'Proxmox'], ['spark', 'Sparks'], ['nas', 'Athena'], ['net', 'Network'], ['power', 'Power'], ['dev', 'Devices']] as const;
export const DOMAINS: DomainDefinition[] = systems.map(([id, focus]) => {
  const prefix = `D_${id}_`;
  const queries = ['latency', 'receive', 'transmit', 'devices'].map((key) => ({ refId: prefix + key, key, source: focus, expr: `synthetic_${key}{system="${id}"}`, instant: key === 'devices', range: key !== 'devices', scope: id }));
  return {
    id, focus, title: focus, accent: 'azure', queries,
    pages: [
      { id: 'summary', title: 'Overview', question: 'Which synthetic resource needs investigation?', required: [prefix + 'latency'], sections: (ctx) => [
        { kind: 'links', id: 'resources', title: 'Resources', items: [{ label: 'device-a', href: ctx.href({ view: 'inventory', entity: 'device-a' }) }] },
        ...(['latency', 'receive', 'transmit'] as const).map((key) => ({ kind: 'chart' as const, id: key, title: `Synthetic ${key}`, refs: [prefix + key], unit: key === 'latency' ? 's' : 'Bps', entityLabels: ['device'] })),
      ] },
      { id: 'inventory', title: 'Resources', question: 'What was observed for this resource?', required: [prefix + 'devices'], sections: (ctx) => [
        { kind: 'text', id: 'identity', title: 'Resource context', text: ctx.entity ?? 'All resources' },
        { kind: 'chart', id: 'resource-receive', title: 'Resource receive', refs: [prefix + 'receive'], unit: 'Bps', entityLabels: ['device'] },
      ] },
    ],
  };
});
export const DOMAIN_SOURCE_GROUPS = DOMAINS.map((domain) => ({ id: domain.id, title: `${domain.title} query source`, focus: domain.focus as Focus, queries: domain.queries }));
export const domainForFocus = (focus: string) => DOMAINS.find((domain) => domain.focus === focus);
export const pageForDomain = (domain: DomainDefinition, view?: string | null) => domain.pages.find((page) => page.id === view) ?? domain.pages[0];
