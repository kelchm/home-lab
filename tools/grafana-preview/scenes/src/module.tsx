import { AppPlugin, type AppRootProps } from '@grafana/data';
import { initPluginTranslations } from '@grafana/i18n';
import { locationService } from '@grafana/runtime';
import { LoadingPlaceholder } from '@grafana/ui';
import { useEffect, useMemo, useRef, useState } from 'react';

import { APP_ID, APP_ROOT, HOME_ALIAS, HOME_URL, type Source } from './contract';
import { useAppLocation } from './hooks';
import { buildApp } from './scene';
import { parseSource } from './urls';

function Root(_: AppRootProps) {
  const [ready, setReady] = useState(false);
  useEffect(() => {
    void initPluginTranslations(APP_ID).then(() => setReady(true));
  }, []);
  return ready ? <Loaded /> : <LoadingPlaceholder text="Loading Lab" />;
}

/**
 * The source is the identity of the whole scene. A change, including Back across one, rebuilds the app rather than
 * patching runners in place, so no replay frame can ever be shown under a live label or the reverse.
 */
function Loaded() {
  const { pathname, search } = useAppLocation();
  const inApp = pathname.startsWith(APP_ROOT);
  const source = useRef<Source>(parseSource(search));
  if (inApp) {
    source.current = parseSource(search);
  }
  const current = source.current;
  const app = useMemo(() => buildApp(current), [current]);
  useEffect(() => {
    if (pathname === APP_ROOT || pathname === HOME_ALIAS) {
      locationService.replace(`${HOME_URL}${search}`);
    }
  }, [pathname, search]);
  return <app.Component key={current} model={app} />;
}

export const plugin = new AppPlugin<{}>().setRootPage(Root);
