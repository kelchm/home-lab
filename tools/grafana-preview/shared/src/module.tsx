import { PanelPlugin } from '@grafana/data';
import { NativePanel } from './NativePanel';
import { VIEWS, type PanelOptions } from './HomeView';

export const plugin = new PanelPlugin<PanelOptions>(NativePanel).setNoPadding().setPanelOptions((builder) =>
  builder
    .addSelect({ path: 'view', name: 'View', defaultValue: 'navigation', settings: { options: VIEWS.map((view) => ({ label: view, value: view })) } })
    .addTextInput({ path: 'focus', name: 'Focus', description: 'Normally the $focus variable.', defaultValue: '$focus' })
    .addBooleanSwitch({ path: 'constrained', name: 'Fit panel height', description: 'Scroll inside the panel with overflow cues.', defaultValue: true })
);
