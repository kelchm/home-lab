import { css } from '@emotion/css';
import { type GrafanaTheme2, locationUtil } from '@grafana/data';
import { sceneGraph, SceneObjectBase, type SceneComponentProps, type SceneObjectState } from '@grafana/scenes';
import { Alert, Badge, ClipboardButton, Field, Input, Modal, Switch, ToolbarButton, useStyles2 } from '@grafana/ui';
import { useState } from 'react';

import { SOURCE_LABELS } from './contract';
import { useAppLocation } from './hooks';
import { CompareShell } from './shell';
import { shareHref } from './urls';

interface ShareControlState extends SceneObjectState {
  isOpen?: boolean;
}

/** A custom substitute for Grafana's Share drawer, which belongs to dashboards. It shares this page's focus, source and range. */
export class ShareControl extends SceneObjectBase<ShareControlState> {
  public static Component = ShareControlRenderer;
}

function ShareControlRenderer({ model }: SceneComponentProps<ShareControl>) {
  const { isOpen } = model.useState();
  return (
    <>
      <ToolbarButton icon="share-alt" tooltip="Share this page, resource and time range" onClick={() => model.setState({ isOpen: true })}>
        Share
      </ToolbarButton>
      {isOpen && <ShareModal model={model} onDismiss={() => model.setState({ isOpen: false })} />}
    </>
  );
}

function ShareModal({ model, onDismiss }: { model: ShareControl; onDismiss: () => void }) {
  const styles = useStyles2(getStyles);
  const { focus, view, entity, source } = sceneGraph.getAncestor(model, CompareShell).useState();
  const [lockRange, setLockRange] = useState(source === 'live');
  const location = useAppLocation();
  const timeRange = sceneGraph.getTimeRange(model).useState();
  const href = shareHref(location, timeRange.value, lockRange);
  const url = `${window.location.origin}${locationUtil.assureBaseUrl(href)}`;
  const relative = typeof timeRange.from === 'string' && timeRange.from.startsWith('now');
  return (
    <Modal isOpen title="Share this view" onDismiss={onDismiss}>
      <div className={styles.stack}>
        <Badge color="purple" icon="wrench" text="Custom tool, not Grafana's Share" />
        <p className={styles.summary}>
          {[focus, view, entity, SOURCE_LABELS[source]].filter(Boolean).join(' · ')}
        </p>
        <Field label="Lock time range" description={relative ? 'This range moves with now. Locking keeps the recipient on the same window.' : 'This range is already absolute, so locking changes nothing but removes auto refresh.'}>
          <Switch id="share-lock-range" value={lockRange} onChange={(event) => setLockRange(event.currentTarget.checked)} />
        </Field>
        <Field label="Link">
          <Input
            id="share-link"
            readOnly
            value={url}
            addonAfter={
              <ClipboardButton icon="copy" variant="primary" getText={() => url}>
                Copy
              </ClipboardButton>
            }
          />
        </Field>
        {source === 'live' && <Alert severity="info" title="Live data keeps changing">A locked link keeps the time window, but the recipient still queries the live source.</Alert>}
      </div>
    </Modal>
  );
}

const getStyles = (theme: GrafanaTheme2) => ({
  stack: css({ display: 'flex', flexDirection: 'column', alignItems: 'stretch', gap: theme.spacing(1), '> :first-child': { alignSelf: 'flex-start' } }),
  summary: css({ margin: 0, color: theme.colors.text.secondary }),
});
