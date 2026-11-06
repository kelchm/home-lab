import type { HomeModel, ReviewModel, Row } from './home';
import type { SystemId } from './systems';

export type MarkKind = 'crit' | 'warn' | 'ring' | 'note' | 'maint' | 'past' | '';

export interface SystemStatus {
  mark: MarkKind;
  text: string;
}

const known = (rows: Row[]) => rows.filter((row) => !row.unknown);

/** Map, briefing and review all read one review model, so they can't disagree. `trusted` is whether that model is current. */
export function statusOf(review: ReviewModel | null, system: SystemId, trusted: boolean, loading: boolean): SystemStatus {
  if (!review) {
    return { mark: '', text: loading ? 'Loading' : 'Unknown' };
  }
  const quiet = trusted && review.confirmed ? 'Nothing flagged' : 'Not confirmed';
  if (system === 'overview') {
    const needing = known(review.action);
    const critical = needing.some((row) => row.level >= 3);
    return { mark: critical ? 'crit' : review.action.length ? 'warn' : '', text: needing.length ? `${needing.length} need action` : trusted && review.confirmed ? '0 need action' : 'Coverage incomplete' };
  }
  const mine = (rows: Row[]) => rows.filter((row) => row.system === system);
  const [action, recovered, observe, maintenance] = [review.action, review.recovered, review.observe, review.maintenance].map(mine);
  const needing = known(action);
  const parts = [
    needing.length && `${needing.length} need action`,
    action.length > needing.length && `${action.length - needing.length} unknown`,
    observe.length && `${observe.length} notice${observe.length > 1 ? 's' : ''}`,
    maintenance.length && `${maintenance.length} maintenance`,
    recovered.length && `${recovered.length} resolved`,
  ].filter(Boolean);
  const critical = needing.some((row) => row.level >= 3);
  const mark: MarkKind = critical ? 'crit' : action.length ? 'warn' : recovered.some((row) => row.level >= 3) ? 'ring' : observe.length ? 'note' : maintenance.length ? 'maint' : recovered.length ? 'past' : '';
  return { mark, text: parts.join(' · ') || quiet };
}

export interface Briefing {
  state: string;
  tone: '' | 'bad' | 'unknown';
  critical: number;
  needing: number;
}

export function briefingOf(model: HomeModel | null, trusted: boolean, loading: boolean, atEnd: string): Briefing {
  const review = model?.review;
  if (!review) {
    return { state: loading ? 'Loading' : 'Review unavailable', tone: loading ? '' : 'unknown', critical: 0, needing: 0 };
  }
  const needing = known(review.action);
  const critical = needing.filter((row) => row.level >= 3).length;
  if (needing.length) {
    const count = `${needing.length} ${needing.length === 1 ? 'item' : 'items'}`;
    return { state: atEnd ? `${count} needing action${atEnd}` : `${count} ${needing.length === 1 ? 'needs' : 'need'} action`, tone: 'bad', critical, needing: needing.length };
  }
  return trusted && review.confirmed ? { state: `No action needed${atEnd}`, tone: '', critical, needing: 0 } : { state: 'Coverage incomplete', tone: 'unknown', critical, needing: 0 };
}
