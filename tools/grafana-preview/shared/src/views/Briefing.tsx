import { briefingOf } from '../model/status';
import { Fit, Icon, statusText } from '../ui/parts';
import { OUTSIDE, type ViewContext } from './context';

export function Briefing({ ctx }: { ctx: ViewContext }) {
  const { model, trusted, loading, atEnd, span, mode, modeLabel, modeTitle, coverage, clock, constrained } = ctx;
  const { state, tone, critical, needing } = briefingOf(model, trusted, loading, atEnd);
  const review = model.review;
  const status = statusText(coverage, clock);
  return (
    <Fit constrained={constrained} label="Daily briefing">
      <section className={`brief ${tone}`} aria-label="Daily briefing" data-state={state}>
        <strong>
          <i className="brief-dot" aria-hidden="true" />
          {state}
        </strong>
        {status && (
          <span className="status" role="status">
            {status}
          </span>
        )}
        {review && (
          <div className="brief-counts">
            {critical > 0 && (
              <span>
                <b>{critical}</b> critical
              </span>
            )}
            <span>
              <b>{review.recovered.length}</b> resolved
            </span>
            <span>
              <b>{review.observe.length}</b> worth a look
            </span>
            <span>
              <b>{review.maintenance.length}</b> maintenance
            </span>
            {needing > 0 && !review.confirmed && <span>Coverage incomplete</span>}
          </div>
        )}
        <div className="brief-period">
          <span className="brief-out" title="These systems link to their existing production dashboards.">
            <Icon name="out" />
            {OUTSIDE}
          </span>
          <span>
            <span className="mode" data-mode={mode} title={modeTitle}>
              {modeLabel}
            </span>
            <span className="hide-sm">{` · ${span} review`}</span>
          </span>
        </div>
      </section>
    </Fit>
  );
}
