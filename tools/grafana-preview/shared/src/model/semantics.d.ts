export type Point = [number, number | null];
export type Band = [number, number, number];

export interface Series {
  labels: Record<string, string>;
  points: Point[];
  step: number | null;
}
export interface FrameLike {
  refId?: string;
  fields: unknown[];
}
export interface DataLike {
  series: FrameLike[];
}

export interface ReviewItem {
  id: string;
  label: string;
  detail?: string;
  level: number;
  kind?: string;
  unknown?: boolean;
  maintenance?: boolean;
  probe?: string;
  start?: number;
  end?: number;
  clipped?: boolean;
  duration?: number;
  episodes?: number;
  uncertain?: boolean;
  labels?: Record<string, string>;
  tab?: string;
  age?: number;
}
export interface DailyCheck {
  id: string;
  name: string;
  assertion: string;
  group: string;
  value: number | null;
  status: 'passing' | 'failed' | 'unknown';
}
export interface DailyWan {
  name: string;
  value: number | null;
  latency: number | null;
  status: 'online' | 'offline' | 'unknown';
}
export interface Review {
  action: ReviewItem[];
  recovered: ReviewItem[];
  observe: ReviewItem[];
  maintenance: ReviewItem[];
  confirmed: boolean;
  checks: DailyCheck[];
  wans: DailyWan[];
}
export interface HostRow {
  instance: string;
  platform: string;
  cpu: number | null;
  memory: number | null;
  up: number | null;
  age: number | null;
  history: Point[];
  state: 'collecting' | 'failing' | 'missing';
}

export function seriesOf(data: DataLike, refId: string): Series[];
export function instant(series: Series | undefined): number | null;
export function scalar(data: DataLike, refId: string): number | null;
export function withGaps(series: Series | undefined): Point[];
export function smallestStep(points: Point[]): number | null;
export function hostRows(data: DataLike): HostRow[];
export function hostsUrl(base: string, host: { platform: string; instance: string }, timeQuery?: string): string;
export function dailyReview(data: DataLike, from: number, to: number): Review;
export function dailyEpisodes(series: Series | undefined, from: number, to: number): Array<{ start: number; end: number }>;
export function dailyIdentity(labels: Record<string, string>): string;
export function dailyBands(series: (Partial<Series> & { windowEnd?: boolean }) | undefined, from: number, to: number): Band[];
export const HOST_STATES: Record<HostRow['state'], { label: string; attention: boolean }>;
