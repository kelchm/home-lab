import type { DomainDefinition, DomainPage } from './contract';
import type { Focus } from '../types';
import { domain as kubernetes } from './kubernetes';
import { domain as proxmox } from './proxmox';
import { domain as sparks } from './sparks';
import { domain as athena } from './athena';
import { domain as network } from './network';
import { domain as power } from './power';
import { domain as devices } from './devices';

// Domain owners supply their definitions here; the host never imports captured samples.
export const DOMAINS: DomainDefinition[] = [kubernetes, proxmox, sparks, athena, network, power, devices];

export const domainForFocus = (focus: string): DomainDefinition | undefined => DOMAINS.find((domain) => domain.focus === focus);

export const pageForDomain = (domain: DomainDefinition, view?: string | null): DomainPage =>
  domain.pages.find((page) => page.id === view) ?? domain.pages[0];

export interface DomainSourceGroup {
  id: string;
  title: string;
  focus: Focus;
  queries: DomainDefinition['queries'];
}

export const DOMAIN_SOURCE_GROUPS: DomainSourceGroup[] = DOMAINS.map((domain) => ({
  id: domain.id, title: `${domain.title} query source`, focus: domain.focus, queries: domain.queries,
}));
