export const isFixturePath = (input) => /(?:^|[\\/])fixtures(?:[\\/]|$)/.test(input);
// Grafana's built-in $__ macros are supported; standalone dashboard variables and their braced forms are not.
export const hasDashboardVariable = (expr) => /\$(?:\{(?!__)[a-zA-Z_]|(?!__)[a-zA-Z_])/.test(expr);

// Stock Grafana requests anonymous user stars even though that identity cannot access the collection.
// Keep this exception tied to the observed endpoint, anonymous selector, origin and exact status text.
export function isAnonymousStarsError(message, origin) {
  if (message.text() !== 'Failed to load resource: the server responded with a status of 403 (Forbidden)') return false;
  try {
    const url = new URL(message.location().url);
    return url.origin === origin && url.pathname === '/apis/collections.grafana.app/v1alpha1/namespaces/default/stars'
      && [...url.searchParams].length === 1 && url.searchParams.get('fieldSelector') === 'metadata.name=user-';
  } catch { return false; }
}
