import type { CSSProperties } from 'react';
import { statusOf } from '../model/status';
import { SYSTEMS, type System } from '../model/systems';
import { Fit, Icon, Mark } from '../ui/parts';
import { OUTSIDE, targetOf, type ViewContext } from './context';

function Item({ system, ctx }: { system: System; ctx: ViewContext }) {
  const status = statusOf(ctx.model.review, system.id, ctx.trusted, ctx.loading);
  const target = targetOf(system, ctx);
  const outside = !system.inComparison;
  const about = `${system.name}, ${status.text}${outside ? `. ${OUTSIDE}, opens the production dashboard` : ''}`;
  const common = { className: 'sys', 'data-system': system.name, 'data-outside': outside || undefined, style: { '--s': `var(--${system.accent})` } as CSSProperties, title: about };
  const inner = (
    <>
      <Icon name={system.icon} />
      <span className="t">
        <b>
          {system.name}
          {outside && <Icon name="out" />}
        </b>
        <small>{status.text}</small>
      </span>
      <Mark kind={status.mark} />
    </>
  );
  if (!target) {
    return (
      <span {...common} aria-label={about}>
        {inner}
      </span>
    );
  }
  return (
    <a {...common} href={target.href} aria-label={about} aria-current={system.name === ctx.focus ? 'page' : undefined} {...(target.outside ? { target: '_blank', rel: 'noopener' } : {})}>
      {inner}
    </a>
  );
}

/** Only the map. Both hosts already own the title, window and source controls, and the briefing states the source. */
export function Navigation({ ctx }: { ctx: ViewContext }) {
  return (
    <Fit constrained={ctx.constrained} label="Systems">
      <nav className="nav" aria-label="Systems">
        {SYSTEMS.map((system) => (
          <Item key={system.id} system={system} ctx={ctx} />
        ))}
      </nav>
    </Fit>
  );
}
