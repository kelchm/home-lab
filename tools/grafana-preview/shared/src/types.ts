import type { PanelData, TimeRange } from '@grafana/data';

export const VIEWS = ['navigation', 'briefing', 'activity', 'review', 'machines', 'network-summary', 'network-inventory', 'checks'] as const;
export type View = (typeof VIEWS)[number];

export type Mode = 'recorded' | 'live';

export const FOCUSES = ['Overview', 'Kubernetes', 'Proxmox', 'Sparks', 'Athena', 'Network', 'Power', 'Devices'] as const;
export type Focus = (typeof FOCUSES)[number];

export interface HomeViewProps {
  data: PanelData;
  timeRange: TimeRange;
  view: View;
  /** One of FOCUSES. Anything else reads as Overview. */
  focus: string;
  /**
   * Ordinary hrefs keyed by system display name. A complete map enables the internal lab navigation.
   * Partial maps retain the original native comparison's external-dashboard fallbacks.
   */
  links: Record<string, string>;
  /** Fit the parent's height and show overflow cues. Unset renders natural height for a page that scrolls. */
  constrained?: boolean;
  width?: number;
  /** Which source these frames came from. Unset falls back to the request's datasource. */
  mode?: Mode;
  /** IANA zone. Defaults to the accepted page's America/New_York. */
  timeZone?: string;
}

export interface PanelOptions {
  view: View;
  /** Interpolated, normally `$focus`. */
  focus: string;
  constrained: boolean;
}

export type Freshness = 'loading' | 'fresh' | 'empty' | 'partial' | 'stale' | 'unavailable' | 'failed';
