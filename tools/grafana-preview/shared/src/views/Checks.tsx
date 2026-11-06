import { num } from '../format';
import type { Check } from '../model/home';
import type { Band } from '../model/semantics';
import { Card, Fit, Status, Strip } from '../ui/parts';
import type { ViewContext } from './context';

const RESULT = { passing: ['Passing', ''], failed: ['Failed', 'bad'], unknown: ['Unknown', 'unknown'] } as const;

const CAVEAT = 'Each check asserts one endpoint response from a single LAN Pi, not a full transaction or an outside view.';

function CheckRow({ check, ctx }: { check: Check; ctx: ViewContext }) {
  const [label, tone] = RESULT[check.status];
  const evidence = [check.http != null && `HTTP ${check.http}`, check.ms != null && `${num(check.ms, check.ms < 10 ? 1 : 0)} ms`].filter(Boolean).join(' · ');
  return (
    <div className="chk" data-check={check.id} data-status={check.status} data-row>
      <div>
        <b>{check.name}</b>
        <small>{check.assertion}</small>
      </div>
      <Strip bands={check.bands} from={ctx.from} to={ctx.to} label={`${check.name} over ${ctx.span}`} clock={ctx.clock} />
      <div className={`res ${tone}`}>
        {label}
        <small>{evidence}</small>
      </div>
    </div>
  );
}

export interface CheckSummary {
  /** `–` when no check reported: missing isn't zero passing. */
  passing: string;
  total: number;
  recovered: number;
  tone: '' | 'bad' | 'unknown';
}

export function summarize(checks: Check[]): CheckSummary {
  const count = (status: Check['status']) => checks.filter((check) => check.status === status).length;
  return {
    passing: count('unknown') === checks.length ? '–' : String(count('passing')),
    total: checks.length,
    recovered: checks.filter((check) => check.status === 'passing' && check.bands.some((band) => band[2] === 0)).length,
    tone: count('failed') ? 'bad' : count('unknown') ? 'unknown' : '',
  };
}

export function Rollup({ bands, summary, hint, ctx }: { bands: Band[]; summary: CheckSummary; hint: string; ctx: ViewContext }) {
  return (
    <>
      <span>
        <b>Service checks</b>
        <small>
          {summary.recovered > 0 && `${summary.recovered} recovered · `}
          {hint}
        </small>
      </span>
      <Strip bands={bands} from={ctx.from} to={ctx.to} label={`All ${summary.total} checks combined over ${ctx.span}`} clock={ctx.clock} />
      <em className={summary.tone}>
        {summary.passing} / {summary.total}
      </em>
    </>
  );
}

export function Checks({ ctx }: { ctx: ViewContext }) {
  const { model, constrained, coverage, clock, span, tag } = ctx;
  const summary = summarize(model.checks);
  const rows = (
    <>
      {model.checks.map((check) => (
        <CheckRow key={check.id} check={check} ctx={ctx} />
      ))}
      <p className="chk-note">{CAVEAT}</p>
    </>
  );
  // In a fixed grid the host's collapsible row is the disclosure. In natural height this view is its own.
  if (!constrained) {
    return (
      <Card eye="Reachability" when={span} tag={tag} className="checks-card" status={<Status coverage={coverage} clock={clock} />}>
        <details className="checks solo">
          <summary>
            <Rollup bands={model.rollup} summary={summary} hint={`open all ${summary.total}`} ctx={ctx} />
          </summary>
          {rows}
        </details>
      </Card>
    );
  }
  return (
    <Card eye="Reachability" when={span} tag={tag} title="Service checks" pill={`${summary.passing} / ${summary.total} passing`} tone={summary.tone} status={<Status coverage={coverage} clock={clock} />}>
      <Fit constrained label="Service checks">
        <div className="chk-list">{rows}</div>
      </Fit>
    </Card>
  );
}
