import { NEEDS } from '@shared/model/catalog';
import { DOMAINS } from '@shared/domains/registry';
import { pageRequiredRefs, type DomainContext, type DomainDefinition, type DomainPage, type DomainScope } from '@shared/domains/contract';
import type { ChartSection } from '@shared/domains/DomainView';
import { COMMON_QUERIES, type Focus, type QueryDef, SOURCE_GROUPS } from './contract';
import type { View } from './home-view';

export interface CardSpec {
  id: string;
  title: string;
  focus?: Focus;
  page?: string;
  view?: View;
  sourceGroup: string;
  queries: QueryDef[];
  nativePanelId?: number;
  entityLabels?: readonly string[];
  entity?: string;
}

const needs = (view: View) => COMMON_QUERIES.filter((query) => NEEDS[view].includes(query.refId));
export const CARDS = {
  common: { id: 'common', title: 'Home query source', sourceGroup: 'common', queries: COMMON_QUERIES, nativePanelId: 1 },
  briefing: { id: 'briefing', title: 'Daily briefing', view: 'briefing', sourceGroup: 'common', queries: needs('briefing'), nativePanelId: 2 },
  activity: { id: 'activity', title: 'Across the lab', focus: 'Overview', view: 'activity', sourceGroup: 'common', queries: needs('activity'), nativePanelId: 3 },
  review: { id: 'review', title: 'What happened', focus: 'Overview', view: 'review', sourceGroup: 'common', queries: needs('review'), nativePanelId: 4 },
  machines: { id: 'machines', title: 'Machines', focus: 'Overview', view: 'machines', sourceGroup: 'common', queries: needs('machines'), nativePanelId: 5 },
  checks: { id: 'checks', title: 'Service checks', focus: 'Overview', view: 'checks', sourceGroup: 'common', queries: needs('checks'), nativePanelId: 6 },
} satisfies Record<string, CardSpec>;

export const SOURCE_CARDS: CardSpec[] = SOURCE_GROUPS.map((group) => ({
  id: group.id, title: group.title, sourceGroup: group.id, queries: group.queries,
  focus: 'focus' in group ? group.focus : undefined,
  nativePanelId: group.id === 'common' ? 1 : undefined,
}));

export function pageCard(domain: DomainDefinition, page: DomainPage, scope?: DomainScope): CardSpec {
  const required = scope ? pageRequiredRefs(page, scope) : page.required;
  return { id: `${domain.id}:${page.id}`, title: `${domain.title} / ${page.title}`, focus: domain.focus as Focus, page: page.id, sourceGroup: domain.id, queries: domain.queries.filter((query) => required.includes(query.refId)) };
}

export function chartCard(domain: DomainDefinition, page: DomainPage, section: ChartSection): CardSpec {
  return { ...pageCard(domain, page), id: `${domain.id}:${page.id}:${section.id}`, title: section.title, entityLabels: section.entityLabels, entity: section.entity, queries: domain.queries.filter((query) => section.refs.includes(query.refId)) };
}

/** Chart definitions are evaluated with the current page context, including direct entity entries. */
export function cardById(id: string | undefined, ctx?: DomainContext): CardSpec | undefined {
  if (!id) return undefined;
  const basic = [...Object.values(CARDS), ...SOURCE_CARDS].find((spec) => spec.id === id);
  if (basic) return basic;
  for (const domain of DOMAINS) for (const page of domain.pages) {
    const spec = pageCard(domain, page, ctx);
    if (spec.id === id) return spec;
    if (ctx && id.startsWith(`${spec.id}:`)) {
      const chart = page.sections(ctx).find((section): section is ChartSection => section.kind === 'chart' && `${spec.id}:${section.id}` === id);
      if (chart) return chartCard(domain, page, chart);
    }
  }
  return undefined;
}

export const refIdsOf = (spec: CardSpec): ReadonlySet<string> => new Set(spec.queries.map((query) => query.refId));
