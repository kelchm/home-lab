import { useInsertionEffect } from 'react';

// The accepted page's stylesheet, scoped under one class so both hosts get the same rules with no global reach.
// Its viewport breakpoints became container queries: a panel's width isn't the page's.
const CSS = `
.hlc{
  --canvas:#060B11;--top:#112131;--bottom:#0B151F;--stroke:rgba(150,205,245,.10);--stroke2:rgba(150,205,245,.18);--grid:rgba(150,205,245,.07);--track:rgba(150,205,245,.09);--pass:#33495C;
  --ink:#E9F1F7;--soft:#BCCAD6;--muted:#9FB1C1;--faint:#7E92A4;
  --azure:#4DB0FF;--sea:#3FE6C4;--orchid:#C685FF;--ok:#4ADE80;--warn:#FFAE1F;--fail:#FF4B6E;--fail2:#FF6684;
  --a:var(--azure);
  container:hlc/inline-size;position:relative;box-sizing:border-box;min-width:0;color:var(--ink);
  font-family:"Inter",-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;font-size:13px;line-height:1.4;font-weight:400;-webkit-font-smoothing:antialiased;

  &[data-constrained]{height:100%;display:flex;flex-direction:column}
  *,*::before,*::after{box-sizing:border-box}
  a{color:inherit;text-decoration:none}
  a:focus-visible,summary:focus-visible,button:focus-visible,.fit-scroll:focus-visible{outline:2px solid var(--azure);outline-offset:2px;border-radius:6px}
  h2,h3,h4,p{margin:0}
  small{font-size:inherit}
  .i{width:16px;height:16px;flex:none;fill:none;stroke:currentColor;stroke-width:1.7;stroke-linecap:round;stroke-linejoin:round}

  /* Fit: a constrained surface scrolls inside itself and says so. */
  .fit{position:relative;flex:1;min-height:0;display:flex;flex-direction:column}
  .fit-scroll{flex:1;min-height:0;display:flex;flex-direction:column;overflow:auto;scrollbar-width:thin;scrollbar-color:rgba(150,205,245,.22) transparent}
  .fit-scroll>div{flex:1 0 auto;min-width:0;display:flex;flex-direction:column}
  &[data-view=navigation] .fit-scroll>div,&[data-view=briefing] .fit-scroll>div{flex:0 0 auto;margin-block:auto}
  .fit::before,.fit::after{content:"";position:absolute;left:0;right:0;height:26px;pointer-events:none;opacity:0;transition:opacity .12s;z-index:2}
  .fit::before{top:0;background:linear-gradient(0deg,transparent,rgba(11,21,31,.92))}
  .fit::after{bottom:0;background:linear-gradient(180deg,transparent,rgba(11,21,31,.96))}
  .fit[data-above]::before,.fit[data-below]::after{opacity:1}
  .fit-cue{position:absolute;right:6px;bottom:4px;z-index:3;display:inline-flex;align-items:center;gap:5px;padding:2px 9px;border:0;border-radius:99px;font:inherit;font-size:11px;line-height:17px;font-weight:500;color:var(--ink);background:rgba(17,33,49,.96);box-shadow:inset 0 0 0 1px var(--stroke2),0 2px 8px rgba(0,0,0,.45);cursor:pointer}
  .fit-cue:hover{background:#17304A}
  .fit-cue::after{content:"";width:5px;height:5px;margin-top:-2px;border-right:1.5px solid currentColor;border-bottom:1.5px solid currentColor;transform:rotate(45deg)}

  /* Shell */
  .mode{font-size:11px;font-weight:500;white-space:nowrap}
  .mode[data-mode=recorded]{color:var(--orchid)}
  .mode[data-mode=live]{color:var(--ok)}
  .mode[data-mode=unknown]{color:var(--warn)}
  .nav{display:grid;grid-template-columns:repeat(8,minmax(0,1fr));gap:6px;margin:0;padding:0}
  .sys{position:relative;display:flex;align-items:center;gap:9px;min-width:0;height:52px;padding:0 11px;border-radius:10px;color:var(--soft);background:rgba(150,205,245,.035);box-shadow:inset 0 0 0 1px var(--stroke);transition:background-color .12s,box-shadow .12s,color .12s}
  a.sys:hover{background:rgba(150,205,245,.075);color:var(--ink)}
  .sys>.i{color:var(--muted)}
  a.sys:hover>.i{color:var(--soft)}
  .sys span.t{display:block;min-width:0;flex:1}
  .sys b{display:flex;align-items:center;gap:4px;font-size:13px;line-height:17px;font-weight:600;color:inherit;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .sys b .i{width:10px;height:10px;color:var(--faint)}
  .sys small{display:block;font-size:11px;line-height:15px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .sys[aria-current=page]{color:var(--ink);background:linear-gradient(180deg,color-mix(in srgb,var(--s) 22%,#0B151F),color-mix(in srgb,var(--s) 9%,#0B151F));box-shadow:inset 0 0 0 1px color-mix(in srgb,var(--s) 62%,transparent)}
  .sys[aria-current=page]::after{content:"";position:absolute;left:12px;right:12px;bottom:-1px;height:2px;border-radius:2px;background:var(--s)}
  .sys[aria-current=page]>.i{color:var(--s)}
  .sys[aria-current=page] small{color:var(--soft)}
  .mark{flex:none;width:9px;height:9px;display:block;margin:0;padding:0;background:none}
  .sys .mark{position:absolute;top:8px;right:9px}
  .mark.crit{background:var(--fail);transform:rotate(45deg);border-radius:1px}
  .mark.warn{width:0;height:0;border-left:5px solid transparent;border-right:5px solid transparent;border-bottom:9px solid var(--warn)}
  .mark.ring{border-radius:50%;box-shadow:inset 0 0 0 2px var(--fail2)}
  .mark.note{border-radius:50%;box-shadow:inset 0 0 0 2px var(--azure)}
  .mark.maint{width:8px;height:8px;border-radius:2px;box-shadow:inset 0 0 0 2px var(--muted)}
  .mark.past{border-radius:50%;box-shadow:inset 0 0 0 2px var(--warn)}
  .g{display:grid;grid-template-columns:repeat(12,minmax(0,1fr));gap:12px}
  .brief{display:flex;align-items:center;flex-wrap:wrap;gap:8px 20px;min-height:38px;padding:2px;color:var(--soft);font-size:12px}
  .brief>strong{display:flex;align-items:center;gap:8px;color:var(--ink);font-size:14px;font-weight:500}
  .brief-dot{width:6px;height:6px;flex:none;border-radius:50%;background:var(--soft)}
  .brief.bad>strong{color:var(--fail2)}
  .brief.bad .brief-dot{background:var(--fail)}
  .brief.unknown>strong{color:var(--warn)}
  .brief.unknown .brief-dot{background:var(--warn)}
  .brief-counts{order:1;display:flex;flex-wrap:wrap;gap:4px 14px;color:var(--muted)}
  .brief-counts b{font-weight:500;color:var(--soft);font-variant-numeric:tabular-nums}
  .brief-period{order:2;display:flex;flex-wrap:wrap;justify-content:flex-end;align-items:center;gap:2px 12px;margin-left:auto;color:var(--muted);font-size:11px;white-space:nowrap}
  .brief-out{display:inline-flex;align-items:center;gap:4px;color:var(--faint)}
  .brief-out .i{width:10px;height:10px}
  .c4{grid-column:span 4}.c6{grid-column:span 6}.c8{grid-column:span 8}.c12{grid-column:span 12}

  /* Status: says what the numbers below can't. Never colours a surface healthy or names an unproved cause. */
  .status{margin:0 0 8px;padding:4px 9px;border-radius:7px;font-size:11.5px;line-height:16px;color:var(--warn);background:rgba(255,174,31,.09);box-shadow:inset 0 0 0 1px rgba(255,174,31,.2)}
  .status.quiet{color:var(--muted);background:none;box-shadow:none;padding-left:0}
  .card>.status{margin:8px 0 0}
  .brief .status{order:0;margin:0;padding:1px 8px}

  /* Focus heading */
  .head{display:flex;align-items:center;gap:16px;min-height:52px;padding:2px;margin-bottom:12px}
  .head .ico{display:grid;place-items:center;width:38px;height:38px;flex:none;border-radius:10px;color:var(--a);background:color-mix(in srgb,var(--a) 13%,transparent);box-shadow:inset 0 0 0 1px color-mix(in srgb,var(--a) 34%,transparent)}
  .head .ico .i{width:19px;height:19px}
  .head .txt{min-width:0;flex:1}
  .head h2{font-size:19px;line-height:24px;font-weight:600;letter-spacing:-.015em}
  .head h2 span{font-size:12px;font-weight:500;letter-spacing:0;color:var(--muted);margin-left:8px}
  .head p{color:var(--soft);font-size:12.5px;line-height:18px}
  .back{display:inline-flex;align-items:center;gap:6px;flex:none;font-size:12px;color:var(--muted);white-space:nowrap}
  .back:hover{color:var(--ink)}
  .back .i{width:14px;height:14px}

  /* Cards */
  .card{--g:color-mix(in srgb,var(--a) 11%,transparent);position:relative;display:flex;flex-direction:column;min-width:0;padding:14px 16px;border-radius:14px;overflow:hidden;background:radial-gradient(420px 200px at 100% 0,var(--g),transparent 70%),linear-gradient(180deg,var(--top),var(--bottom));box-shadow:inset 0 0 0 1px var(--stroke)}
  &[data-constrained]>.card:not(.mon){flex:1;min-height:0}
  .card.bad{--g:rgba(255,75,110,.14)}
  .card>header{display:flex;align-items:flex-start;justify-content:space-between;gap:12px}
  .card>header>div{min-width:0}
  .eye{font-size:10.5px;line-height:14px;letter-spacing:.055em;text-transform:uppercase;color:var(--muted);font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .eye b{font-weight:600;color:var(--a)}
  &[data-constrained][data-errored] .eye,&[data-constrained][data-errored] .head,&[data-constrained][data-errored] .brief{padding-left:14px}
  .card h3{font-size:15px;line-height:20px;font-weight:600;letter-spacing:-.008em;color:var(--ink)}
  .pill{flex:none;padding:2px 10px;border-radius:99px;font-size:11.5px;line-height:18px;font-weight:500;color:var(--soft);box-shadow:inset 0 0 0 1px var(--stroke2);white-space:nowrap;font-variant-numeric:tabular-nums}
  .pill.bad{color:var(--fail2);background:rgba(255,75,110,.11);box-shadow:none}
  .pill.unknown{color:var(--warn);background:rgba(255,174,31,.1);box-shadow:none}
  .note{margin-top:4px;font-size:11.5px;line-height:16px;color:var(--muted)}
  .meta{margin-top:6px;font-size:11px;line-height:15px;color:var(--muted)}
  .meta b{font-weight:500;color:var(--soft)}
  .plot{position:relative;min-width:0;touch-action:pan-y}
  .plot svg{position:absolute;inset:0;width:100%;height:100%;display:block;overflow:visible}
  .strip{position:relative;display:block;height:8px;border-radius:2px;overflow:hidden;background:repeating-linear-gradient(135deg,rgba(150,205,245,.13) 0 2px,transparent 2px 5px)}
  .strip i{position:absolute;top:0;bottom:0;background:var(--pass)}
  .strip i.f{background:var(--fail);min-width:2px;z-index:1}

  /* The system map */
  .map{padding-bottom:8px}
  .map .rows{margin-top:10px}
  .map .sumrow{flex:none}
  .map .keyline{flex:none}
  .lane{display:grid;grid-template-columns:150px minmax(0,1fr) 92px;gap:0 14px;align-items:stretch;min-height:62px;border-top:1px solid var(--grid)}
  .lane:hover{background:linear-gradient(90deg,transparent,rgba(150,205,245,.028) 20%,rgba(150,205,245,.028) 80%,transparent)}
  .lane[data-state=retained] .lp,.lane[data-state=retained] .lv{opacity:.55}
  .lane>a,.lane>.lh{display:flex;flex-direction:column;justify-content:center;gap:1px;min-width:0;padding:6px 0;border-radius:6px}
  .lane b.n{display:flex;align-items:center;gap:7px;font-size:13px;line-height:17px;font-weight:600;white-space:nowrap}
  .lane b.n .i{width:15px;height:15px;color:var(--l)}
  .lane b.n .i.o{width:10px;height:10px;margin-left:-3px;color:var(--faint)}
  .lane>a:hover b.n{color:var(--l)}
  .lane small{display:block;font-size:11px;line-height:15px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .lane .lp{height:62px;margin:0}
  &[data-constrained] .lane{flex:1 0 auto}
  &[data-constrained] .lane .lp{height:auto;min-height:62px}
  &[data-constrained] .map .rows{flex:1 0 auto;display:flex;flex-direction:column;margin-top:10px}
  .lane .lv{display:flex;flex-direction:column;align-items:flex-end;justify-content:center;gap:0;min-width:0;text-align:right}
  .lane .lv b{font-size:16px;line-height:20px;font-weight:600;letter-spacing:-.015em;font-variant-numeric:tabular-nums}
  .lane .lv small{font-size:10.5px;line-height:14px}
  .axis{position:relative;display:grid;grid-template-columns:150px minmax(0,1fr) 92px;gap:0 14px;height:22px;border-top:1px solid var(--grid)}
  .axis div{position:relative;grid-column:2}
  .axis span{position:absolute;top:5px;transform:translateX(-50%);font-size:10.5px;color:var(--muted);white-space:nowrap}
  .keyline{display:flex;flex-wrap:wrap;align-items:center;gap:4px 14px;padding-top:7px;border-top:1px solid var(--grid);font-size:10.5px;line-height:14px;color:var(--muted)}
  .keyline i{display:inline-block;width:13px;height:4px;margin-right:6px;border-radius:1px;vertical-align:middle;background:var(--c)}
  .keyline i.w{width:14px;height:10px;background:rgba(255,75,110,.28);box-shadow:inset 0 -3px 0 var(--fail)}
  .keyline .end{margin-left:auto}
  .checks{border-top:1px solid var(--grid)}
  .checks.solo{border-top:0}
  .card.checks-card{padding:2px 16px}
  .sumrow,.checks summary{display:grid;grid-template-columns:150px minmax(0,1fr) 92px;gap:0 14px;align-items:center;min-height:40px;list-style:none}
  .checks summary{grid-template-columns:150px minmax(0,1fr) 104px}
  .checks summary{cursor:pointer}
  .checks summary::-webkit-details-marker{display:none}
  .sumrow b,.checks summary b{display:flex;align-items:center;gap:6px;font-size:12.5px;font-weight:600}
  .checks summary b::before{content:"";width:6px;height:6px;flex:none;border-right:1.5px solid var(--muted);border-bottom:1.5px solid var(--muted);transform:rotate(-45deg);transition:transform .12s}
  .checks[open] summary b::before{transform:rotate(45deg) translate(-1px,-1px)}
  .sumrow small,.checks summary small{display:block;font-size:11px;line-height:15px;color:var(--muted)}
  .checks summary small{margin-left:12px}
  .sumrow em,.checks summary em{font-style:normal;text-align:right;font-size:12px;font-weight:600;font-variant-numeric:tabular-nums}
  .sumrow em.bad,.checks summary em.bad{color:var(--fail2)}
  .sumrow em.unknown,.checks summary em.unknown{color:var(--warn)}
  .chk{display:grid;grid-template-columns:150px minmax(0,1fr) 104px;gap:0 14px;align-items:center;min-height:33px;border-top:1px solid var(--grid)}
  .fit .chk{flex:1}
  .chk-list{flex:1;display:flex;flex-direction:column;margin-top:8px}
  .chk div{min-width:0}
  .chk b{display:block;font-size:12px;line-height:15px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .chk small{display:block;font-size:10.5px;line-height:13px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .chk .res{text-align:right;font-size:11.5px;line-height:15px;font-variant-numeric:tabular-nums}
  .chk .res.bad{color:var(--fail2)}
  .chk .res.unknown{color:var(--warn)}
  .chk-note{margin:0;padding:7px 0 3px;font-size:10.5px;color:var(--muted);border-top:1px solid var(--grid)}

  /* Exceptions */
  .ex-list{flex:1;min-height:0;margin-top:10px}
  .ex-g{margin-top:12px}
  .ex-g:first-child{margin-top:0}
  .ex-g h4{margin:0 0 2px;font-size:10.5px;line-height:15px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:var(--muted)}
  .ex{display:grid;grid-template-columns:14px minmax(0,1fr) auto;gap:0 8px;align-items:start;padding:6px 7px;margin:0 -7px;border-radius:7px}
  a.ex:hover,a.ex:focus-visible{background:rgba(150,205,245,.07)}
  .ex .mark{margin-top:4px}
  .ex b{display:block;font-size:12.5px;line-height:17px;font-weight:600;overflow-wrap:anywhere}
  .ex small{display:block;font-size:11px;line-height:15px;color:var(--muted);overflow-wrap:anywhere}
  .ex .w{font-size:11px;line-height:17px;color:var(--muted);white-space:nowrap}
  .quiet{margin:8px 0 12px;font-size:13px;color:var(--soft)}

  /* Machines */
  .mtable{width:100%;margin-top:10px;border-collapse:collapse;table-layout:fixed}
  .mtable th{padding:0 8px 7px;font-size:10.5px;line-height:14px;letter-spacing:.05em;text-transform:uppercase;font-weight:500;color:var(--muted);text-align:left;white-space:nowrap;background:none;border:0}
  .fit .mtable th{position:sticky;top:0;z-index:1;background:#0E1C29}
  .mtable th:nth-child(2),.mtable td.mo{white-space:nowrap}
  .mtable th small{margin-left:6px;font-size:10px;letter-spacing:0;text-transform:none;color:var(--faint)}
  .mtable td{height:37px;padding:0 8px;border-top:1px solid var(--grid);vertical-align:middle;min-width:0}
  .mname{display:flex;align-items:baseline;gap:7px;min-width:0}
  .mname a,.mname>span:first-child{font-size:12.5px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .mname a:hover{color:var(--azure)}
  .mname small{font-size:10.5px;color:var(--muted)}
  .state{display:inline-flex;align-items:center;gap:7px;font-size:12px;color:var(--soft);white-space:nowrap}
  .state i{width:7px;height:7px;border-radius:50%;background:var(--c,var(--ok))}
  .meter{display:flex;align-items:center;gap:9px}
  .meter b{flex:none;width:50px;text-align:right;font-size:12px;font-weight:600;font-variant-numeric:tabular-nums;white-space:nowrap}
  .track{display:block;position:relative;flex:1;height:5px;min-width:18px;border-radius:3px;background:var(--track);overflow:hidden}
  .track i{position:absolute;left:0;top:0;bottom:0;border-radius:3px;background:var(--c,var(--azure))}
  .msp{display:block;width:100%;height:24px;overflow:visible}
  .age{text-align:right;font-size:12px;color:var(--soft);font-variant-numeric:tabular-nums;white-space:nowrap}
  .mon{flex:none;flex-direction:row;display:flex;flex-wrap:wrap;align-items:center;gap:5px 18px;margin-top:12px;padding:10px 16px;font-size:12px;color:var(--muted)}
  .mon strong{font-weight:600;color:var(--soft)}
  .mon .unknown{color:var(--warn)}

  /* Generic panels */
  .bars{display:flex;flex-direction:column;gap:8px;margin-top:12px}
  .bar>div{display:flex;justify-content:space-between;gap:10px;font-size:12px;line-height:16px}
  .bar>div span{min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--soft)}
  .bar>div b{flex:none;font-weight:600;font-variant-numeric:tabular-nums}
  .bar>div b small{font-weight:400;font-size:11px;color:var(--muted)}
  .bar .track{margin-top:5px;flex:none}
  .stats{display:grid;grid-template-columns:repeat(var(--n,4),minmax(0,1fr));gap:16px;margin-top:12px}
  .stats>div{min-width:0}
  .stats b{display:block;font-size:22px;line-height:27px;font-weight:600;letter-spacing:-.02em;font-variant-numeric:tabular-nums;white-space:nowrap}
  .stats b small{font-size:12px;font-weight:500;letter-spacing:0;color:var(--muted);margin-left:2px}
  .stats span{display:block;font-size:11.5px;line-height:15px;color:var(--muted)}
  .tbl{width:100%;margin-top:10px;border-collapse:collapse}
  .tbl th{padding:0 7px 6px;font-size:10.5px;line-height:14px;letter-spacing:.05em;text-transform:uppercase;font-weight:500;color:var(--muted);text-align:left;white-space:nowrap;background:none;border:0}
  .tbl th.r,.tbl td.r{text-align:right}
  .tbl td{padding:6px 7px;border-top:1px solid var(--grid);font-size:12px;line-height:16px;color:var(--soft);font-variant-numeric:tabular-nums;white-space:nowrap}
  .tbl td:first-child,.tbl th:first-child{padding-left:0}
  .tbl td:last-child,.tbl th:last-child{padding-right:0}
  .tbl td b{font-weight:600;color:var(--ink)}
  .tbl td small{font-size:10.5px;color:var(--muted);margin-left:5px}
  .tbl-wrap{min-width:0;overflow-x:auto}
  .deep{display:flex;flex-wrap:wrap;align-items:center;gap:6px 8px;margin-top:12px;padding:11px 16px;border-radius:12px;box-shadow:inset 0 0 0 1px var(--stroke);font-size:12px;color:var(--muted)}
  .deep strong{margin-right:6px;font-weight:600;color:var(--soft)}
  .deep a{display:inline-flex;align-items:center;gap:4px;padding:3px 9px;border-radius:7px;color:var(--soft);background:rgba(150,205,245,.06)}
  .deep a:hover{color:var(--ink);background:rgba(150,205,245,.12)}
  .deep a .i{width:11px;height:11px;color:var(--muted)}
  .deep em{flex-basis:100%;font-style:normal;font-size:11px;color:var(--faint)}
  .empty{display:grid;place-items:center;height:100%;min-height:42px;padding:6px 10px;text-align:center;font-size:12px;color:var(--muted);background:repeating-linear-gradient(135deg,rgba(150,205,245,.05) 0 2px,transparent 2px 7px);border-radius:8px}
  .plot .empty{position:absolute;inset:4px 0}
  [data-state=failed] .empty,[data-state=unanswered] .empty{color:var(--warn)}

  @container hlc (max-width:1180px){
    .sys{flex-direction:column;justify-content:center;gap:3px;height:58px;padding:0 3px;text-align:center}
    .sys span.t{flex:none;width:100%}
    .sys b{justify-content:center;font-size:12px;line-height:15px}
    .sys small{font-size:10.5px;line-height:13px}
  }
  @container hlc (max-width:900px){
    .c4,.c8{grid-column:span 12}
    .hide-md{display:none}
    .mtable td{padding:0 5px}
    .mtable th{padding:0 5px 7px}
    .radios{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px 22px}
  }
  @container hlc (max-width:760px){
    .c6{grid-column:span 12}
    .sys small{display:none}
    .sys b{display:block}
    .sys b .i{position:absolute;top:7px;left:7px}
  }
  @container hlc (max-width:620px){
    .nav{grid-template-columns:repeat(4,minmax(0,1fr));gap:5px}
    .sys{height:46px}
    .sys b{font-size:11.5px}
    .sys>.i{width:15px;height:15px}
    .brief{gap:2px 12px;min-height:0;padding:0 2px}
    .brief>strong{flex-basis:100%;font-size:13px;line-height:18px}
    .brief .status{flex-basis:100%}
    .brief-counts{gap:2px 10px;font-size:11.5px;line-height:16px}
    .brief-period{margin-left:0;justify-content:flex-start;line-height:15px}
    .g{gap:10px}
    .radios{grid-template-columns:minmax(0,1fr)}
    .head{flex-wrap:wrap;gap:10px 12px;min-height:0}
    .head .txt{flex-basis:calc(100% - 52px)}
    .head .back{display:none}
    .lane{grid-template-columns:minmax(0,1fr) auto;grid-template-rows:auto 50px;gap:0 10px;padding-top:4px}
    .lane .lp{grid-column:1/3;grid-row:2;height:50px}
    &[data-constrained] .lane .lp{height:50px;min-height:0}
    .lane>a,.lane>.lh{padding:4px 0 0}
    .lane .lv{flex-direction:row;align-items:baseline;gap:4px;padding-top:3px}
    .axis{grid-template-columns:minmax(0,1fr);gap:0}
    .axis div{grid-column:1}
    .sumrow,.checks summary{grid-template-columns:minmax(0,1fr) auto;gap:5px 10px;padding:7px 0}
    .sumrow .strip,.checks summary .strip{grid-column:1/3;grid-row:2}
    .chk{grid-template-columns:minmax(0,1fr) auto;gap:3px 10px;padding:6px 0}
    .chk .strip{grid-column:1/3;grid-row:2}
    .mtable,.mtable tbody{display:block}
    .mtable thead,.mtable colgroup{display:none}
    .mtable tr{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));align-items:center;gap:5px 10px;padding:8px 0;border-top:1px solid var(--grid)}
    .mtable td{display:block;height:auto;padding:0;border:0;min-width:0}
    .mtable td:first-child{grid-column:1/5}
    .mtable td.mo{grid-column:5/7;grid-row:1;text-align:right}
    .mtable td.mo .state{justify-content:flex-end}
    .mtable td.mc{grid-column:1/3;grid-row:2}.mtable td.ms{grid-column:3/5;grid-row:2}.mtable td.mm{grid-column:5/7;grid-row:2}
    .mtable td.ma{display:none}
    .meter{gap:6px}.meter b{width:auto;min-width:41px;font-size:11.5px}
    .msp{height:20px}
    .stats{grid-template-columns:repeat(2,minmax(0,1fr))}
    .hide-sm{display:none}
    .card{padding:12px}
    .card.checks-card{padding:2px 12px}
  }
  @media (prefers-reduced-motion:reduce){*{transition:none!important}}
}
`;

const ID = 'homelab-compare-styles';

/** One stylesheet however many views a page mounts. It stays in the document: it's scoped, and the next view needs it. */
export function useStyles() {
  useInsertionEffect(() => {
    if (!document.getElementById(ID)) {
      const style = document.createElement('style');
      style.id = ID;
      style.textContent = CSS;
      document.head.appendChild(style);
    }
  }, []);
}
