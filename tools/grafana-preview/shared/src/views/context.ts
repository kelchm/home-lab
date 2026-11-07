import type { TimeRange } from '@grafana/data';
import type { Clock } from '../format';
import type { Coverage, Resolved } from '../lifecycle';
import type { HomeModel } from '../model/home';
import type { NetworkModel } from '../model/network';
import { GRAFANA, type System } from '../model/systems';
import type { Focus, Mode } from '../types';

export interface ViewContext {
  model: HomeModel;
  network: NetworkModel | null;
  resolved: Resolved;
  /** This surface's state, from the queries its own claims rest on. */
  coverage: Coverage;
  /** Whether the review is current and complete, the only state allowed to read as quiet. */
  trusted: boolean;
  loading: boolean;
  clock: Clock;
  from: number;
  to: number;
  /** The window's length, as `24 h`. */
  span: string;
  /** The source tag on each card, empty when the figures are live and current. */
  tag: string;
  /** ` at window end` unless the window ends now: historical data never reads as current. */
  atEnd: string;
  mode: Mode | 'unknown';
  modeLabel: string;
  modeTitle: string;
  links: Record<string, string>;
  constrained: boolean;
  focus: Focus;
  timeRange: TimeRange;
}

const edge = (value: unknown) => (typeof value === 'string' ? value : Number(value));

export const deepHref = (uid: string, range: TimeRange) => `${GRAFANA}/d/${uid}?from=${encodeURIComponent(edge(range.raw.from))}&to=${encodeURIComponent(edge(range.raw.to))}`;

export const timeQuery = (range: TimeRange) => `from=${encodeURIComponent(edge(range.raw.from))}&to=${encodeURIComponent(edge(range.raw.to))}`;

export interface Target {
  href: string;
  outside: boolean;
}

/** Overview and Network come from the host. The others go to their existing dashboard, in a new tab and marked as such. */
export function targetOf(system: System, ctx: Pick<ViewContext, 'links' | 'timeRange'>): Target | null {
  const supplied = ctx.links[system.name];
  if (supplied) {
    return { href: supplied, outside: false };
  }
  return system.inComparison ? null : { href: deepHref(system.deeper[0][1], ctx.timeRange), outside: true };
}

export const OUTSIDE = 'Outside this comparison';
