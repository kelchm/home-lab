import type { DomainCell, DomainContext, DomainDefinition, DomainSection } from '../contract';
import { format } from '../data';
import { queries, ref, units, type InputState, type Unit } from './model';

const status = (state: InputState): DomainCell => ({
  text: { battery: 'On battery', mains: 'Mains reported', 'not-asserted': 'OB flag not asserted', conflicting: 'Conflicting flags', unknown: 'Unknown' }[state],
  tone: state === 'battery' || state === 'conflicting' ? 'warning' : state === 'unknown' ? 'unknown' : 'neutral',
  detail: state === 'not-asserted' ? 'Online flag not captured; this is not confirmation of mains.' : undefined,
});
const detailHref = (ctx: DomainContext, unit: string) => ctx.href({ focus: 'Power', view: 'unit', entity: unit });

function inventory(ctx: DomainContext): DomainSection {
  return {
    kind: 'table', id: 'ups-inventory', title: 'Three independent UPS units',
    description: 'Window-end measurements. Runtime is the UPS estimate at its reported load; these units are not a combined runtime pool.',
    columns: ['UPS / detail', 'Input status', 'Load', 'Estimated runtime', 'Charge', 'Input voltage'],
    rows: units(ctx).map((unit) => ({ id: unit.id, cells: [
      { text: unit.name, href: detailHref(ctx, unit.id), detail: unit.source }, status(unit.inputState),
      format(unit.load, 1, '%'), format(unit.runtime, 1, ' min'), format(unit.charge, 1, '%'), format(unit.inputVoltage, 1, ' V'),
    ] })), empty: 'No UPS identity available.',
  };
}

function charts(ctx: DomainContext, selected = false): DomainSection[] {
  const chart = (key: string, title: string, unit: string, description: string): DomainSection => ({
    kind: 'chart', id: `${key}-history`, title, refs: [ref(`${key}History`)], unit, height: 230, description,
    ...(selected ? { entityLabels: ['ups'] } : {}),
  });
  return [
    chart('load', 'UPS load', 'percent', 'Compare each unit over the same window. Percent is the UPS-reported load, not watts.'),
    chart('runtimeMin', 'Estimated battery runtime', 'm', 'Minutes at the load estimated by each UPS. Missing measurements stay gaps; charge alone does not establish operating margin.'),
    ...(ctx.mode === 'live' ? [
      chart('charge', 'Battery charge', 'percent', 'Reported state of charge; battery condition, transfer behavior and shutdown coordination require separate evidence.'),
      chart('input', 'Input voltage', 'volt', 'Measured input voltage. Voltage alone does not establish online operation or transfer success.'),
    ] : []),
  ];
}

function limits(): DomainSection {
  return { kind: 'text', id: 'power-evidence', title: 'What establishes operating margin?', text: 'Read runtime alongside load and the input-state flags. A full battery is not a resilience test. Battery condition, successful transfer, connected equipment, shutdown coordination and recovery after an outage are not established by these measurements. Missing runtime remains unknown even when charge is 100%.' };
}

function tools(ctx: DomainContext, unit?: Unit): DomainSection {
  return { kind: 'links', id: 'power-tools', title: 'Investigate the collection path', items: [
    ...(unit?.id === 'network-rack' || !unit ? [{ label: 'Athena / UPS and hardware', href: ctx.deeper('synology-health', { node: 'nas' }), description: 'Native Synology UPS metrics for the network-rack unit.' }] : []),
    ...(unit && unit.id !== 'network-rack' ? [{ label: `${unit.collector} host metrics`, href: ctx.deeper('hosts', { platform: 'pi', node: unit.collector }), description: 'Inspect the Pi collecting NUT telemetry.' }, { label: `${unit.collector} device detail`, href: ctx.href({ focus: 'Devices', view: 'host', entity: unit.collector }), description: 'Supporting-host resources, thermals and time service.' }] : []),
    { label: 'All UPS units', href: ctx.href({ focus: 'Power', view: 'overview', entity: null }), description: 'Compare load and runtime in the same source and time range.' },
  ] };
}

function overview(ctx: DomainContext): DomainSection[] {
  return [inventory(ctx), ...charts(ctx), limits(), tools(ctx)];
}

function detail(ctx: DomainContext): DomainSection[] {
  if (!ctx.entity) return [inventory(ctx), { kind: 'table', id: 'collection-paths', title: 'Inventory and collection paths', columns: ['UPS', 'Collector host', 'Collector status'], rows: units(ctx).map((unit) => ({ id: unit.id, cells: [{ text: unit.name, href: detailHref(ctx, unit.id) }, unit.collector, unit.collection] })), empty: 'No inventory available.' }, limits()];
  const unit = units(ctx).find((item) => item.id === ctx.entity);
  if (!unit) return [{ kind: 'text', id: 'unit-unknown', title: 'UPS identity unavailable', tone: 'unknown', text: 'This entity does not identify one of the three configured UPS units.', links: [{ label: 'UPS inventory', href: ctx.href({ focus: 'Power', view: 'unit', entity: null }) }] }];
  return [
    { kind: 'text', id: 'unit-identity', title: unit.name, text: `${unit.source}. Collector host: ${unit.collector}. ${unit.collection}. A successful exporter scrape does not test battery transfer or the equipment it protects.` },
    { kind: 'metrics', id: 'unit-observations', title: 'Window-end operating context', items: [
      { label: 'Input state', value: status(unit.inputState).text, tone: status(unit.inputState).tone, detail: status(unit.inputState).detail },
      { label: 'Load', value: format(unit.load, 1, '%'), detail: 'Reported load fraction, not measured watts.' },
      { label: 'Estimated runtime', value: format(unit.runtime, 1, ' min'), detail: 'Estimate at reported load; unknown is not zero.' },
      { label: 'Battery charge', value: format(unit.charge, 1, '%'), detail: 'State of charge does not measure battery condition.' },
      { label: 'Input voltage', value: format(unit.inputVoltage, 1, ' V') },
    ] }, ...charts(ctx, true), limits(), tools(ctx, unit),
  ];
}

export const domain: DomainDefinition = {
  id: 'power', focus: 'Power', title: 'Power', accent: 'orchid', queries,
  pages: [
    { id: 'overview', title: 'Operating margin', question: 'How do load, runtime and input state compare across the three UPS units?', required: ['load', 'runtimeMin', 'charge', 'input', 'onBattery'].map(ref), sections: overview },
    { id: 'unit', title: 'UPS inventory & detail', question: 'Which unit and collection path explain this observation?', required: ['load', 'runtimeMin', 'charge', 'input', 'onBattery'].map(ref), sections: detail },
  ],
};
