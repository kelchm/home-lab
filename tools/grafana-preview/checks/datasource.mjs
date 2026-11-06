// Track the complete datasource boundary, including asynchronous body parsing and requests with no response.
export function observeDatasource(page) {
  const responses = [], failures = [], abortedNavigations = [];
  const pending = new Set(), active = new Set(), navigationRequests = new Set(), records = new WeakMap();
  const isQuery = (url) => new URL(url).pathname === '/api/ds/query';
  page.on('request', (request) => { if (isQuery(request.url())) active.add(request); });
  page.on('response', (response) => {
    if (!isQuery(response.url())) return;
    const task = (async () => {
      const record = { url: response.url(), status: response.status(), results: [] };
      const request = response.request();
      records.set(request, record);
      responses.push(record);
      try {
        const body = await response.json();
        if (!body?.results || Array.isArray(body.results) || typeof body.results !== 'object' || !Object.keys(body.results).length) {
          throw new Error('Datasource response has no query results');
        }
        const expected = request.postDataJSON()?.queries?.map((query) => query.refId) ?? [];
        if (expected.some((ref) => !Object.hasOwn(body.results, ref))) throw new Error('Datasource response omits requested query results');
        record.results = Object.entries(body.results).map(([ref, result]) => {
          if (!result || typeof result !== 'object' || Array.isArray(result) || (!Array.isArray(result.frames) && !result.error)) throw new Error(`Malformed query result ${ref}`);
          return {
            ref, status: result.status, frames: result.frames?.length ?? 0, error: result.error,
            rows: (result.frames ?? []).reduce((sum, frame) => sum + (frame.data?.values?.[0]?.length ?? 0), 0),
            latest: Math.max(0, ...(result.frames ?? []).flatMap((frame) => {
              const time = frame.schema?.fields?.findIndex((field) => field.type === 'time');
              return time >= 0 ? (frame.data?.values?.[time] ?? []) : [];
            })),
          };
        });
      } catch (error) {
        record.error = error.message;
      }
    })();
    pending.add(task);
    void task.finally(() => pending.delete(task));
  });
  page.on('requestfinished', (request) => { active.delete(request); navigationRequests.delete(request); });
  page.on('requestfailed', (request) => {
    if (!isQuery(request.url())) return;
    const record = { url: request.url(), error: request.failure()?.errorText ?? 'Request failed without a response' };
    // Only an abort of a request already in flight when our explicit navigation began is expected.
    const navigationAbort = navigationRequests.has(request) && /(?:ERR_ABORTED|NS_BINDING_ABORTED)/.test(record.error);
    (navigationAbort ? abortedNavigations : failures).push(record);
    if (navigationAbort && records.has(request)) records.get(request).abortedNavigation = true;
    active.delete(request); navigationRequests.delete(request);
  });
  return {
    responses, failures, abortedNavigations,
    async drain() { while (pending.size) await Promise.all([...pending]); },
    async navigate(action) {
      const interrupted = [...active];
      interrupted.forEach((request) => navigationRequests.add(request));
      return action();
    },
    get inFlight() { return [...active].map((request) => request.url()); },
    get transportOk() { return responses.length > 0 && active.size === 0 && !failures.length && responses.every((response) => response.status === 200); },
    get evaluationOk() { const completed = responses.filter((response) => !response.abortedNavigation); return completed.length > 0 && completed.every((response) => !response.error && response.results.length > 0 && response.results.every((result) => !result.error && (result.status == null || result.status === 200))); },
  };
}
