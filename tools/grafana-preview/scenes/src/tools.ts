import { locationUtil, type PanelData, type PanelMenuItem } from '@grafana/data';
import { locationService } from '@grafana/runtime';
import { getExploreURL, sceneGraph, type SceneObject, type SceneObjectRef, VizPanelExploreButton, VizPanelMenu } from '@grafana/scenes';

import { access } from './access';
import { type CardSpec, refIdsOf } from './cards';
import { scopeData } from './data';
import { type ShareControl } from './share';
import { inspectHref } from './urls';

/** Past this, Explore would open dozens of queries at once, so the menu sends the viewer to pick one instead. */
const MAX_EXPLORE_QUERIES = 12;

export async function exploreHref(model: SceneObject, refIds: ReadonlySet<string>, supplied?: PanelData) {
  const data = supplied ?? sceneGraph.getData(model).state.data;
  if (!data) {
    return '';
  }
  const { from, to } = sceneGraph.getTimeRange(model).state;
  return getExploreURL(scopeData(data, refIds), model, { from, to });
}

/** Viewer tools for one surface. Every entry that is not stock Grafana says so in its label. */
export function toolItems(spec: CardSpec, share: SceneObjectRef<ShareControl>): PanelMenuItem[] {
  const location = locationService.getLocation();
  return [
    { text: 'Inspect data (custom)', iconClassName: 'table', href: inspectHref(location, spec.id, 'data') },
    { text: 'Inspect query (custom)', iconClassName: 'info-circle', href: inspectHref(location, spec.id, 'query') },
    { text: 'Share view (custom)', iconClassName: 'share-alt', onClick: () => share.resolve().setState({ isOpen: true }) },
    { text: 'Edit…', iconClassName: 'edit', href: inspectHref(location, spec.id, 'change') },
  ];
}

export function toolMenu(spec: CardSpec, share: SceneObjectRef<ShareControl>) {
  const populate = (menu: VizPanelMenu) => {
    menu.setItems(toolItems(spec, share));
    if (!access().canExplore) {
      return;
    }
    if (spec.queries.length > MAX_EXPLORE_QUERIES) {
      const href = inspectHref(locationService.getLocation(), spec.id, 'query');
      menu.addItem({ text: 'Explore one query…', iconClassName: 'compass', href });
      return;
    }
    void exploreHref(menu, refIdsOf(spec)).then((href) => {
      if (href) {
        menu.addItem({ text: 'Explore', iconClassName: 'compass', href: locationUtil.assureBaseUrl(href) });
      }
    });
  };
  return new VizPanelMenu({ $behaviors: [populate] });
}

/** Stock Explore button. Viewers without `datasources:explore` get nothing rather than a link that core refuses. */
export const exploreButton = () => (access().canExplore ? new VizPanelExploreButton() : undefined);
