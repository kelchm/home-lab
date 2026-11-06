import type { TimeRange } from '@grafana/data';
import type { Coverage } from '../lifecycle';
import type { FrameLike } from '../model/semantics';
import type { Accent, SystemId } from '../model/systems';

/** Query definitions contain expressions and capture locators, never recorded samples. */
export interface DomainQuery {
  refId: string;
  key: string;
  source: string;
  expr: string;
  instant: boolean;
  range: boolean;
  scope: string;
  capture?: { kind: 'bounded' | 'inference' | 'catalog'; key: string; range?: boolean };
  optional?: boolean;
}

export interface DomainRoute {
  focus?: string;
  view?: string;
  entity?: string | null;
  from?: string | number;
  to?: string | number;
}

export interface DomainContext {
  series: readonly FrameLike[];
  timeRange: TimeRange;
  mode: 'recorded' | 'live';
  timeZone: string;
  entity: string | null;
  coverage: Coverage;
  href: (route: DomainRoute) => string;
  deeper: (uid: string, variables?: Record<string, string>) => string;
}

/** Required evidence may differ between recordings; it must not depend on whether a query succeeded. */
export type DomainScope = Pick<DomainContext, 'timeRange' | 'mode' | 'entity'>;

export interface DomainCell {
  text: string;
  href?: string;
  tone?: 'good' | 'warning' | 'bad' | 'unknown' | 'neutral';
  detail?: string;
}

export interface DomainRow {
  id: string;
  cells: Array<string | DomainCell>;
}

export type DomainSection =
  | { kind: 'text'; id: string; title: string; text: string; tone?: 'warning' | 'bad' | 'unknown' | 'neutral'; links?: Array<{ label: string; href: string }> }
  | { kind: 'metrics'; id: string; title: string; items: Array<{ label: string; value: string; detail?: string; tone?: DomainCell['tone']; href?: string }> }
  | { kind: 'table'; id: string; title: string; description?: string; columns: string[]; rows: DomainRow[]; empty: string }
  | { kind: 'chart'; id: string; title: string; description?: string; refs: string[]; unit: string; height?: number; entityLabels?: string[]; entity?: string }
  | { kind: 'links'; id: string; title: string; items: Array<{ label: string; href: string; description?: string }> };

export interface DomainPage {
  id: string;
  title: string;
  question: string;
  /** References required for this page's claims, not every optional signal in the domain. */
  required: readonly string[];
  /** Replaces `required` for the selected window/source/resource when provided. */
  requiredFor?: (scope: DomainScope) => readonly string[];
  sections: (ctx: DomainContext) => DomainSection[];
}

export function pageRequiredRefs(page: DomainPage, scope: DomainScope): readonly string[] {
  return page.requiredFor?.(scope) ?? page.required;
}

export interface DomainDefinition {
  id: Exclude<SystemId, 'overview'>;
  focus: string;
  title: string;
  accent: Accent;
  queries: DomainQuery[];
  pages: DomainPage[];
}
