import { useInsertionEffect } from 'react';

const CSS = `
.hlc[data-internal] .brief-out{display:none}
.lab-domain{--azure:#4DB0FF;--sea:#3FE6C4;--orchid:#C685FF;--ink:#E9F1F7;--soft:#BCCAD6;--muted:#9FB1C1;--stroke:rgba(142,184,214,.17);--accent:var(--azure);color:var(--ink);font:13px/1.55 Inter,system-ui,sans-serif;min-width:0;container:lab-domain / inline-size}
.lab-domain[data-accent=sea]{--accent:var(--sea)}.lab-domain[data-accent=orchid]{--accent:var(--orchid)}
.lab-domain *{box-sizing:border-box}.lab-domain h2,.lab-domain h3,.lab-domain p{margin:0}
.lab-domain a{color:var(--azure);text-decoration:none}.lab-domain a:hover{text-decoration:underline}.lab-domain a:focus-visible,.lab-domain [tabindex]:focus-visible{outline:2px solid var(--accent);outline-offset:4px;border-radius:3px}
.lab-domain .domain-head{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;margin:4px 0 16px}.lab-domain .domain-head h2{font-size:24px;font-weight:600;letter-spacing:-.02em;line-height:1.25}.lab-domain .domain-head p{color:var(--soft);margin-top:6px;max-width:82ch}.lab-domain .domain-eyebrow{font-size:11px;letter-spacing:.055em;text-transform:uppercase;color:var(--accent);margin-bottom:6px}
.lab-domain .domain-back{white-space:nowrap;font-size:12px}.lab-domain .domain-tabs{display:flex;flex-wrap:wrap;gap:4px 8px;border-bottom:1px solid var(--stroke);padding:0 0 8px;margin:0 0 16px}.lab-domain .domain-tabs a{color:var(--muted);padding:6px 12px;border-radius:7px}.lab-domain .domain-tabs a[aria-current]{color:var(--ink);background:color-mix(in srgb,var(--accent) 12%,transparent);box-shadow:inset 0 -2px var(--accent)}
.lab-domain .domain-context{display:flex;flex-wrap:wrap;gap:6px 16px;align-items:baseline;margin:0 0 14px;color:var(--muted);font-size:11.5px}.lab-domain .domain-entity{color:var(--soft);overflow-wrap:anywhere}.lab-domain .domain-status{padding:9px 12px;border:1px solid rgba(255,190,90,.25);background:rgba(255,190,90,.06);color:#edc68f;border-radius:8px;margin:0 0 14px}
.lab-domain .domain-sections{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;align-items:start}.lab-domain .domain-section{grid-column:1/-1;min-width:0;border:1px solid var(--stroke);border-radius:12px;padding:18px 20px;background:linear-gradient(125deg,color-mix(in srgb,var(--accent) 5%,#0f1c29),#0d1721 78%)}
.lab-domain .domain-section[data-kind=chart]{grid-column:span 1;padding:10px 12px}.lab-domain .domain-section h3{font-size:15px;font-weight:600;letter-spacing:-.01em;line-height:1.4;margin-bottom:7px}.lab-domain .domain-section p{max-width:100ch;white-space:pre-line;color:var(--soft)}.lab-domain .domain-description{font-size:12px;color:var(--muted);margin-bottom:12px!important}
.lab-domain .domain-metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(180px,100%),1fr));gap:16px 24px;margin:14px 0 0}.lab-domain .domain-metrics div{min-width:0}.lab-domain .domain-metrics dt{font-size:12px;color:var(--muted);margin-bottom:4px}.lab-domain .domain-metrics dd{margin:0;font-size:21px;line-height:1.3;font-variant-numeric:tabular-nums;overflow-wrap:anywhere}.lab-domain .domain-detail{display:block;font-size:11.5px;color:var(--muted);margin-top:4px;line-height:1.5}
.lab-domain .domain-section [data-tone=good]{color:#8cdbc3}.lab-domain .domain-section [data-tone=warning]{color:#edc68f}.lab-domain .domain-section [data-tone=bad]{color:#ff9cae}.lab-domain .domain-section [data-tone=unknown]{color:var(--muted)}
.lab-domain .domain-table-wrap{overflow-x:auto;max-width:100%;margin-top:12px}.lab-domain table{width:100%;border-collapse:collapse;text-align:left}.lab-domain th{font-size:11px;font-weight:500;color:var(--muted);padding:0 16px 9px 0;vertical-align:bottom;white-space:nowrap}.lab-domain td{border-top:1px solid var(--stroke);padding:11px 16px 11px 0;vertical-align:top;color:var(--soft);font-size:12px;min-width:90px;font-variant-numeric:tabular-nums}.lab-domain th:last-child,.lab-domain td:last-child{padding-right:0}.lab-domain td:first-child{font-weight:500;color:var(--ink)}.lab-domain .domain-empty{padding:12px 0;color:var(--muted)}
.lab-domain .domain-links{list-style:none;display:flex;flex-wrap:wrap;gap:12px 24px;margin:12px 0 0;padding:0}.lab-domain .domain-links li{max-width:55ch}.lab-domain .domain-retained{opacity:.55}.lab-domain .domain-chart-state{font-size:11px;color:var(--muted);margin:5px 4px}.lab-domain .domain-tools{display:flex;justify-content:flex-end;font-size:11px;margin-top:12px}
@container lab-domain (max-width:820px){.lab-domain .domain-section[data-kind=chart]{grid-column:1/-1}.lab-domain .domain-head{flex-wrap:wrap}.lab-domain .domain-section{padding:14px}.lab-domain .domain-metrics{gap:14px}.lab-domain .domain-tabs{gap:4px}.lab-domain .domain-tabs a{padding:6px 9px}}
@media(prefers-reduced-motion:reduce){.lab-domain *{transition:none!important}}
`;

export function useDomainStyles() {
  useInsertionEffect(() => {
    if (document.getElementById('homelab-domain-styles')) return;
    const style = document.createElement('style');
    style.id = 'homelab-domain-styles';
    style.textContent = CSS;
    document.head.appendChild(style);
  }, []);
}
