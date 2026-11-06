import { LoadingState, type PanelData } from '@grafana/data';
import { type SceneDataProvider, type SceneDataProviderResult, type SceneDataState, SceneObjectBase, type SceneObjectRef } from '@grafana/scenes';
import { map, merge, type Observable } from 'rxjs';

/** Narrows data to the queries a consumer owns. Errors and request targets narrow with it, so Explore and Inspect see the same scope as the chart. */
export function scopeData(data: PanelData, refIds?: ReadonlySet<string>): PanelData {
  if (!refIds) {
    return data;
  }
  const errors = (data.errors ?? (data.error ? [data.error] : []))?.filter((error) => !error.refId || refIds.has(error.refId));
  const hasErrors = Boolean(errors?.length);
  return {
    ...data,
    state: data.state === LoadingState.Error && !hasErrors ? LoadingState.Done : data.state,
    series: data.series.filter((frame) => frame.refId !== undefined && refIds.has(frame.refId)),
    errors: hasErrors ? errors : undefined,
    error: errors?.[0],
    request: data.request && { ...data.request, targets: data.request.targets.filter((target) => refIds.has(target.refId)) },
  };
}

/** Combines sources in order. Loading wins over Error so a refresh in progress is never reported as finished. */
export function mergeData(parts: Array<PanelData | undefined>): PanelData | undefined {
  const present = parts.filter((part): part is PanelData => Boolean(part));
  if (present.length < 2) {
    return present[0];
  }
  const errors = present.flatMap((part) => part.errors ?? (part.error ? [part.error] : []));
  const states = present.map((part) => part.state);
  const first = present[0];
  return {
    ...first,
    state: states.includes(LoadingState.Loading) ? LoadingState.Loading : states.includes(LoadingState.Error) ? LoadingState.Error : first.state,
    series: present.flatMap((part) => part.series),
    errors: errors.length ? errors : undefined,
    error: errors[0],
    structureRev: present.reduce((sum, part) => sum + (part.structureRev ?? 0), 0),
    request: first.request && { ...first.request, targets: present.flatMap((part) => part.request?.targets ?? []) },
  };
}

interface SharedFramesState extends SceneDataState {
  sources: Array<SceneObjectRef<SceneDataProvider>>;
  refIds?: ReadonlySet<string>;
}

/**
 * Reuses frames from one or more query runners without running anything itself.
 *
 * It does what DataProviderProxy does, with two additions this host needs. DataProviderProxy copies `data` only in its constructor,
 * so a consumer that activates after the source finished, such as a Network card opened from Overview, stays empty until the next query.
 * This re-reads on activation. It also merges common and Network frames and can scope them to the refIds a chart owns.
 */
export class SharedFrames extends SceneObjectBase<SharedFramesState> implements SceneDataProvider {
  public constructor(state: SharedFramesState) {
    super(state);
    this.addActivationHandler(() => {
      for (const source of this.state.sources) {
        this._subs.add(
          source.resolve().subscribeToState((next, previous) => {
            if (next.data !== previous.data) {
              this.publish();
            }
          })
        );
      }
      this.publish();
    });
  }

  private read() {
    const merged = mergeData(this.state.sources.map((source) => source.resolve().state.data));
    return merged && scopeData(merged, this.state.refIds);
  }

  private publish() {
    this.setState({ data: this.read() });
  }

  public isDataReadyToDisplay() {
    return this.state.sources.every((source) => source.resolve().isDataReadyToDisplay?.() ?? true);
  }

  public cancelQuery() {
    this.state.sources.forEach((source) => source.resolve().cancelQuery?.());
  }

  public getResultsStream(): Observable<SceneDataProviderResult> {
    return merge(...this.state.sources.map((source) => source.resolve().getResultsStream())).pipe(
      map((result) => ({ origin: this, data: this.read() ?? result.data }))
    );
  }
}
