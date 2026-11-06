import { css } from '@emotion/css';
import { type GrafanaTheme2, LoadingState } from '@grafana/data';
import { type SceneComponentProps, sceneGraph, SceneObjectBase, type SceneObjectState, type VizPanelMenu } from '@grafana/scenes';
import { Dropdown, IconButton, useStyles2 } from '@grafana/ui';
import { useMemo } from 'react';

import { type CardSpec } from './cards';
import { type Source } from './contract';
import { HomeView, type Mode, type View } from './home-view';
import { useAppLocation, useElementWidth } from './hooks';
import { CompareShell } from './shell';
import { focusLinks } from './urls';

export type ViewSpec = CardSpec & { view: View };

const MODES: Record<Source, Mode> = { replay: 'recorded', live: 'live' };

/** Grafana's zone setting can be a keyword, and the shared renderer takes an IANA zone. */
const ianaZone = (zone: string) => (zone === 'browser' ? Intl.DateTimeFormat().resolvedOptions().timeZone : zone === 'utc' ? 'UTC' : zone);

interface HomeCardState extends SceneObjectState {
  spec: ViewSpec;
  menu?: VizPanelMenu;
}

/**
 * Renders one shared view at its natural height, which VizPanel cannot do without the experimental fit scope.
 * The tool menu is the same public VizPanelMenu a VizPanel uses; it is hosted in a Dropdown because there is no PanelChrome here.
 */
export class HomeCard extends SceneObjectBase<HomeCardState> {
  public static Component = HomeCardRenderer;
}

function HomeCardRenderer({ model }: SceneComponentProps<HomeCard>) {
  const styles = useStyles2(getStyles);
  const { spec, menu } = model.useState();
  const { focus, source } = sceneGraph.getAncestor(model, CompareShell).useState();
  const { data } = sceneGraph.getData(model).useState();
  const range = sceneGraph.getTimeRange(model);
  const { value: timeRange } = range.useState();
  const location = useAppLocation();
  const links = useMemo(() => focusLinks(location), [location]);
  const [ref, width] = useElementWidth<HTMLElement>();
  const panelData = useMemo(() => data ?? { state: LoadingState.Loading, series: [], timeRange }, [data, timeRange]);
  return (
    <section ref={ref} className={styles.card} data-card={spec.id} aria-label={spec.title}>
      <HomeView
        data={panelData}
        timeRange={timeRange}
        view={spec.view}
        focus={focus}
        links={links}
        constrained={false}
        width={width}
        mode={MODES[source]}
        timeZone={ianaZone(range.getTimeZone())}
      />
      {menu && (
        <div className={styles.tools}>
          <Dropdown placement="bottom-end" overlay={() => <menu.Component model={menu} />}>
            <IconButton name="ellipsis-v" size="sm" variant="secondary" aria-label={`Tools for ${spec.title}`} />
          </Dropdown>
        </div>
      )}
    </section>
  );
}

const getStyles = (theme: GrafanaTheme2) => {
  const tools = css({
    position: 'absolute',
    // Straddles the card's top edge, because the corner inside belongs to the view's status pill.
    top: theme.spacing(-1.25),
    right: theme.spacing(1.5),
    zIndex: 1,
    display: 'flex',
    opacity: 0,
    border: `1px solid ${theme.colors.border.medium}`,
    borderRadius: theme.shape.radius.pill,
    background: theme.colors.background.primary,
    transition: 'opacity 120ms',
    '@media (prefers-reduced-motion: reduce)': { transition: 'none' },
    '@media (hover: none)': { opacity: 1 },
  });
  return {
    tools,
    card: css({ position: 'relative', minWidth: 0, [`&:hover > .${tools}, &:focus-within > .${tools}`]: { opacity: 1 } }),
  };
};
