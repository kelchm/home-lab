import { dur, type Clock } from '../format';
import type { Row } from '../model/home';
import type { MarkKind } from '../model/status';
import { SYSTEM } from '../model/systems';
import { setHighlight } from '../ui/highlight';
import { Card, Fit, Mark, Status } from '../ui/parts';
import { targetOf, type ViewContext } from './context';

type Tier = 'action' | 'resolved' | 'note' | 'maint';

function timing(row: Row, clock: Clock) {
  if (row.start != null && row.end != null && row.duration != null) {
    const spans = (row.episodes ?? 0) > 1 ? ` · ${row.episodes} ${row.kind === 'probe' ? 'failed' : 'firing'} spans` : '';
    return `${row.clipped ? 'before window' : clock.hm(row.start)}–${clock.hm(row.end)} · ${row.clipped ? '≥' : row.uncertain ? '~' : ''}${dur(row.duration)}${row.uncertain ? ' observed' : ''}${spans}`;
  }
  return row.age != null ? `firing ~${dur(row.age * 1000)}` : '';
}

const markOf = (row: Row, tier: Tier): MarkKind =>
  tier === 'maint' ? 'maint' : tier === 'note' ? 'note' : row.level >= 3 ? (tier === 'resolved' ? 'ring' : 'crit') : tier === 'resolved' ? 'past' : 'warn';

const SEVERITY: Record<Tier, (row: Row) => string> = {
  action: (row) => (row.unknown ? 'Unknown' : row.level >= 3 ? 'Critical' : 'Warning'),
  resolved: (row) => (row.level >= 3 ? 'Critical, resolved' : 'Warning, resolved'),
  note: () => 'Notice',
  maint: () => 'Maintenance',
};

/** A known probe incident opens its evidence page while keeping the host's time/source context. */
export function reviewTarget(row: Row, ctx: Pick<ViewContext, 'links' | 'timeRange'>) {
  const target = targetOf(SYSTEM[row.system], ctx);
  if (!target || target.outside || row.id !== 'probe/athena-dsm') return target;
  const url = new URL(target.href, 'https://preview.invalid');
  url.searchParams.set('focus', 'Athena');
  url.searchParams.set('view', 'incident');
  url.searchParams.set('entity', 'athena-dsm');
  url.searchParams.delete('inspect');
  url.searchParams.delete('inspectTab');
  return { ...target, href: /^https?:\/\//.test(target.href) ? url.href : `${url.pathname}${url.search}${url.hash}` };
}

function ReviewRow({ row, tier, ctx }: { row: Row; tier: Tier; ctx: ViewContext }) {
  const system = SYSTEM[row.system];
  const target = reviewTarget(row, ctx);
  const detail = [row.detail?.replace('//', '/'), timing(row, ctx.clock)].filter(Boolean).join(' · ');
  const label = tier === 'resolved' && row.kind === 'probe' ? `${row.label} check failed` : row.label;
  const inner = (
    <>
      <Mark kind={markOf(row, tier)} />
      <span>
        <b>{label}</b>
        <small>{detail}</small>
      </span>
      <span className="w">{system.name}</span>
    </>
  );
  const about = `${SEVERITY[tier](row)}: ${label}${detail ? `, ${detail}` : ''}, ${system.name}`;
  if (!target) {
    return (
      <div className="ex" data-item={row.id} data-row aria-label={about}>
        {inner}
      </div>
    );
  }
  const light = () => setHighlight({ system: row.system, spans: row.spans });
  const dim = () => setHighlight(null);
  return (
    <a className="ex" href={target.href} data-item={row.id} data-row aria-label={about} onPointerEnter={light} onPointerLeave={dim} onFocus={light} onBlur={dim} {...(target.outside ? { target: '_blank', rel: 'noopener' } : {})}>
      {inner}
    </a>
  );
}

export function Review({ ctx }: { ctx: ViewContext }) {
  const { model, span, tag, constrained, coverage, clock, trusted, loading } = ctx;
  const review = model.review;
  const groups: Array<[string, Row[], Tier]> = review
    ? [
        ['Needs action', review.action, 'action'],
        [`Resolved in ${span}`, review.recovered, 'resolved'],
        ['Worth a look', review.observe, 'note'],
        ['Maintenance', review.maintenance, 'maint'],
      ]
    : [];
  const filled = groups.filter(([, rows]) => rows.length);
  const quiet = trusted && review?.confirmed;
  return (
    <Card eye="Review" when={span} tag={tag} title="What happened" tone={review?.action.some((row) => !row.unknown) ? 'bad' : ''} className="rev" status={<Status coverage={coverage} clock={clock} />}>
      <Fit constrained={constrained} label="What happened">
        <div className="ex-list">
          {filled.map(([name, rows, tier]) => (
            <section className="ex-g" key={tier} data-group={tier}>
              <h4>
                {name} · {rows.length}
              </h4>
              {rows.map((row) => (
                <ReviewRow key={row.id} row={row} tier={tier} ctx={ctx} />
              ))}
            </section>
          ))}
          {!filled.length && !loading && <p className="quiet">{quiet ? 'Nothing to report in this window.' : 'A quiet window can’t be confirmed from what loaded.'}</p>}
        </div>
      </Fit>
    </Card>
  );
}
