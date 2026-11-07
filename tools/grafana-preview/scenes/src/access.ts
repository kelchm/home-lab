import { config } from '@grafana/runtime';

/** The same permissions core checks before it offers Explore and Edit in a panel menu. */
export function access() {
  const user = config.bootData?.user;
  const canEditByRole = user?.orgRole === 'Admin' || user?.orgRole === 'Editor';
  const permitted = (action: string, fallback: boolean) => user?.permissions == null ? fallback : user.permissions[action] === true;
  return {
    canExplore: Boolean(config.exploreEnabled) && permitted('datasources:explore', canEditByRole || Boolean(config.viewersCanEdit)),
    canEdit: permitted('dashboards:write', canEditByRole),
  };
}
