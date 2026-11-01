// The pinned Next.js server offers stock TRMNL OTA automatically. Custom
// boards in this trial keep their own firmware. Loaded before Next's fetch
// wrapper, this blocks only its firmware release check and binary CDN.
const upstreamFetch = globalThis.fetch;
globalThis.fetch = function trialFetch(input, init) {
  const url = new URL(typeof input === "string" || input instanceof URL ? input : input.url);
  const firmwareCheck = url.hostname === "api.github.com" && url.pathname === "/repos/usetrmnl/trmnl-firmware/releases/latest";
  if (firmwareCheck || url.hostname === "trmnl-fw.s3.us-east-2.amazonaws.com") {
    return Promise.resolve(new Response(null, { status: 404 }));
  }
  return upstreamFetch.call(this, input, init);
};
