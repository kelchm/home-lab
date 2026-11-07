// Daily-check semantics. No DOM, no invented health score, no missing-to-zero coercion.
const DAILY_PROBES = [
  {id:'kanidm',name:'Authentication',assertion:'Kanidm status endpoint',group:'Services'},
  {id:'homepage',name:'Homepage',assertion:'Authentication boundary',group:'Services'},
  {id:'jellyfin',name:'Jellyfin',assertion:'Web client responds',group:'Services'},
  {id:'grafana',name:'Grafana',assertion:'Login redirect',group:'Services'},
  {id:'cloudflare-tunnel',name:'Public tunnel',assertion:'Public echo endpoint',group:'Services'},
  {id:'k8s-gateway',name:'Internal DNS',assertion:'Auth address resolves',group:'Dependencies'},
  {id:'gateway-services',name:'Services gateway',assertion:'TCP / 443',group:'Dependencies'},
  {id:'gateway-admin',name:'Admin gateway',assertion:'TCP / 443',group:'Dependencies'},
  {id:'athena-dsm',name:'Athena DSM',assertion:'NAS login page',group:'Dependencies'},
  {id:'unifi-gateway',name:'UniFi gateway',assertion:'ICMP reachability',group:'Dependencies'},
];
const DAILY_TARGETS = {
  kanidm:'https://auth.home.kelch.io/status', homepage:'https://home.kelch.io/',
  jellyfin:'https://jellyfin.home.kelch.io/web/', grafana:'https://grafana.home.kelch.io/',
  'cloudflare-tunnel':'https://echo.kelch.io/', 'k8s-gateway':'10.32.130.2:53',
  'gateway-services':'10.32.140.1:443', 'gateway-admin':'10.32.130.1:443',
  'athena-dsm':'https://athena.home.kelch.io:5001/', 'unifi-gateway':'10.32.1.1',
};
const dailyChecks = (series, ref='A') => {
  const values=new Map(series.filter(s=>s.ref===ref).map(s=>[s.labels.probe,s.value]));
  return DAILY_PROBES.map(p=>({...p,value:values.get(p.id)??null,status:values.get(p.id)===1?'passing':values.get(p.id)===0?'failed':'unknown'}));
};
const dailyWorkloads = (get, list) => {
  const kinds=['Deployments','StatefulSets','DaemonSets'];
  const at=(ref,kind)=>list(ref).find(s=>s.labels.kind===kind)?.value??null;
  const parts=kinds.map(kind=>({kind,total:at('A',kind),ready:at('B',kind),missing:at('C',kind)}));
  const observed=get('F')===1&&get('G')!=null&&get('G')<=90&&parts.every(p=>[p.total,p.ready,p.missing].every(v=>v!=null));
  const total=parts.reduce((n,p)=>n+(p.total??0),0),ready=parts.reduce((n,p)=>n+(p.ready??0),0),missing=parts.reduce((n,p)=>n+(p.missing??0),0);
  return {parts,observed,total:observed?total:null,ready:observed?ready:null,missing:observed?missing:null,unavailable:observed?Math.max(0,total-ready-missing):null};
};
const dailyBackup = get => {
  const expected=get('A'),recent=get('B'),stale=get('C'),never=get('D'),missing=get('E');
  const coverage=get('F'),target=get('G'),age=get('I'),degraded=get('J');
  const observed=coverage===100&&age!=null&&age<=90&&expected!=null&&missing!=null;
  const complete=observed&&missing===0&&[recent,stale,never].every(v=>v!=null)&&recent+stale+never===expected;
  return {expected,recent,stale,never,missing,coverage,target,degraded,observed,complete,
    good:complete&&recent===expected&&target===1,
    gap:observed&&[stale,never,missing].some(v=>v>0)};
};
const dailyWan = (list) => ['T-Mobile (WAN1)','Starlink (WAN2)'].map(name=>{
  const at=ref=>list(ref).find(s=>s.labels.wan===name)?.value??null;
  const v=at('A'),age=at('G');
  return {name,value:v,latency:at('B'),status:age==null||age>180||v==null?'unknown':v===1?'online':v===0?'offline':'unknown'};
});
const dailyBands = (series, from, to) => {
  const ps=(series?.points||[]).filter(p=>p[0]!=null).sort((a,b)=>a[0]-b[0]);
  const step=series?.step??smallestStep(ps)??60000;
  const result=[];
  ps.forEach(([t,v],i)=>{
    if(v!==0&&v!==1)return;
    const start=Math.max(from,series?.windowEnd?t-step:t),end=Math.min(to,series?.windowEnd?t:t+step,series?.windowEnd?Infinity:ps[i+1]?.[0]??Infinity);
    if(end<=start)return;
    const prev=result.at(-1);
    if(prev&&prev[2]===v&&prev[1]===start)prev[1]=end;else result.push([start,end,v]);
  });
  return result;
};
const dailyRecoveries = (checks, histories) => checks.filter(p=>p.status==='passing').flatMap(p=>{
  const s=histories.find(s=>s.labels.probe===p.id),failures=s?.points.filter(p=>p[1]===0)||[];
  const last=failures.at(-1);
  return last?[{...p,lastFailed:last[0]}]:[];
}).sort((a,b)=>b.lastFailed-a.lastFailed);
const dailyGroups = series => {
  const groups=new Map();
  for(const s of series){
    if(s.value==null||s.value<=0)continue;
    const l=s.labels,key=[l.system,l.what,s.value].join('/');
    if(!groups.has(key))groups.set(key,{system:l.system||'unassigned',what:l.what||'Signal',severity:s.value,maintenance:l.what==='NodeSecurityUpdatesPending',members:[]});
    groups.get(key).members.push({thing:l.thing||l.system||'System',detail:l.detail||'',tab:l.tab||''});
  }
  return [...groups.values()].sort((a,b)=>b.severity-a.severity||a.what.localeCompare(b.what));
};

// A single action model feeds both the headline and review, independent of source order.
const dailyIdentity=l=>l.probe?'probe/'+l.probe:l.volume&&/Backup/.test(l.alertname||'')?'backup/'+l.volume:l.namespace&&(l.deployment||l.statefulset||l.daemonset)?'controller/'+l.namespace+'/'+(l.deployment||l.statefulset||l.daemonset):[l.alertname,l.instance||l.node||l.pod||l.name||l.ups||l.namespace||l.cluster||'',l.source_namespace||'',l.source||'',l.destination||'',l.traffic_direction||'',l.path||''].join('/');
const dailyEpisodes=(series,from,to)=>{
  const bands=dailyBands({...series,windowEnd:true},from,to);
  return bands.filter(b=>b[2]===0).map(b=>({start:b[0],end:b[1],clipped:b[0]<=from,uncertain:!bands.some(p=>p[2]===1&&p[0]===b[1]),duration:b[1]-b[0]}));
};
const dailyReview=(data,from,to)=>{
  const list=r=>seriesOf(data,r).map(s=>({...s,value:instant(s)})),get=r=>scalar(data,r),map=new Map();
  const raw=list('AL'),history=seriesOf(data,'AH'),ages=new Map(list('AT').map(s=>[dailyIdentity(s.labels),s.value]));
  const names={NodeSecurityUpdatesPending:'Security updates pending',ProbeFailed:'Check failed',TooManyLogs:'Unusual log volume',NodeSupplyVoltageLow:'Node supply voltage low',RequestErrorsToAPI:'API request errors'};
  const human=s=>names[s]||s.replace(/([a-z])([A-Z])/g,'$1 $2');
  const put=item=>{
    const prior=map.get(item.id);
    if(prior){map.set(item.id,{...prior,...item,level:Math.max(prior.level,item.level),detail:[...new Set([prior.detail,item.detail].filter(Boolean))].join(' · '),alert:prior.alert||item.alert});}
    else map.set(item.id,item);
  };
  for(const g of dailyGroups(list('A'))){
    const candidates=raw.filter(a=>a.labels.alertname===g.what);
    const a=candidates.find(a=>g.members.some(m=>[a.labels.instance,a.labels.probe,a.labels.name,a.labels.ups].includes(m.thing)))||(candidates.length===1?candidates[0]:null);
    const l=a?.labels||{},id=a?dailyIdentity(l):[g.what,g.system,g.members.map(m=>m.thing).join(',')].join('/');
    put({id,label:g.what==='Running hot'&&g.members.every(m=>m.thing==='ups-office')?'Pi temperature margin':human(g.what),detail:g.members.map(m=>m.thing).join(', ')+(g.members[0]?.detail&&!g.members[0].detail.startsWith('alert')?' · '+g.members[0].detail:''),level:g.severity,maintenance:g.maintenance,alert:!!a,what:g.what,labels:l,tab:g.members[0]?.tab||'',kind:'rule',age:ages.get(id)});
  }
  // Raw alerts ensure objects lacking a Home system mapping are still represented.
  for(const a of raw){
    const l=a.labels,id=dailyIdentity(l);if(map.has(id)||[...map.values()].some(x=>x.what===l.alertname&&[l.instance,l.probe,l.name,l.ups].filter(Boolean).some(v=>x.detail?.split(', ').includes(v))))continue;
    put({id,label:human(l.alertname),detail:[l.namespace,l.instance||l.node||l.pod||l.volume].filter(Boolean).join('/'),level:l.severity==='critical'?3:l.severity==='warning'?2:1,maintenance:l.alertname==='NodeSecurityUpdatesPending',alert:true,what:l.alertname,labels:l,kind:'rule',age:ages.get(id)});
  }
  const checks=dailyChecks(list('P').map(s=>({...s,ref:'P'})),'P'),http=new Map(list('PC').map(s=>[s.labels.probe,s.value]));
  const missingChecks=checks.filter(p=>p.status==='unknown');
  if(missingChecks.length===10)put({id:'coverage/checks',label:'Check coverage unavailable',detail:'All 10 expected checks are unknown',level:2,unknown:true,kind:'probe'});
  for(const p of checks.filter(p=>p.status!=='passing'&&missingChecks.length!==10))put({id:'probe/'+p.id,label:p.name+(p.status==='failed'?' check failed':' check unknown'),detail:p.status==='unknown'?'No fresh check sample':http.get(p.id)>0?'HTTP '+http.get(p.id)+' · assertion failed':p.assertion+' failed',level:p.status==='failed'?3:2,kind:'probe',probe:p.id,unknown:p.status==='unknown'});
  const w=dailyWorkloads(r=>get('WK'+r),r=>list('WK'+r));
  if(!w.observed||w.missing>0&&!list('WKX').length)put({id:'coverage/controllers',label:'Controller coverage incomplete',detail:'Inventory or availability status missing',level:2,unknown:true,kind:'controller'});
  for(const s of list('WKN'))put({id:'controller/'+s.labels.namespace+'/'+s.labels.controller,label:s.labels.namespace+'/'+s.labels.controller,detail:s.labels.kind+' · '+s.value+' replicas below desired',level:2,kind:'controller',namespace:s.labels.namespace});
  for(const s of list('WKX'))put({id:'controller/'+s.labels.namespace+'/'+s.labels.controller,label:s.labels.namespace+'/'+s.labels.controller,detail:s.labels.kind+' · availability status missing',level:2,unknown:true,kind:'controller',namespace:s.labels.namespace});
  if(w.observed&&w.unavailable>0&&!list('WKN').length)put({id:'coverage/controller-names',label:'Controllers below desired',detail:w.unavailable+' affected · identities unavailable',level:2,kind:'controller'});
  const b=dailyBackup(r=>get('BK'+r));
  if(!b.observed)put({id:'coverage/backups',label:'Backup coverage incomplete',detail:'Longhorn manager scrapes or inventory incomplete',level:2,unknown:true,kind:'backup'});
  if(b.observed&&!b.complete&&!b.gap)put({id:'coverage/backup-accounting',label:'Backup accounting incomplete',detail:'Fresh, stale and never-backed-up counts do not reconcile',level:2,unknown:true,kind:'backup'});
  if(b.observed&&b.target!==1)put({id:'backup/target',label:'Backup target '+(b.target===0?'unavailable':'unknown'),detail:'Longhorn default target',level:2,unknown:b.target==null,kind:'backup'});
  for(const s of list('BKV'))put({id:'backup/'+s.labels.volume,label:[s.labels.pvc_namespace,s.labels.pvc||s.labels.volume].filter(Boolean).join('/'),detail:s.labels.problem==='stale'?'Backup older than 30 h':s.labels.problem==='never'?'No successful backup yet':'Backup timestamp missing',level:2,unknown:s.labels.problem==='missing',kind:'backup',namespace:s.labels.pvc_namespace,pvc:s.labels.pvc});
  if(b.observed&&b.gap&&!list('BKV').length)put({id:'coverage/backup-names',label:'Backup freshness gap',detail:'Affected volume identities unavailable',level:2,kind:'backup'});
  if(b.degraded>0&&![...map.values()].some(s=>/Volumes (degraded|faulted)|Longhorn.*(Degraded|Faulted)/i.test(s.what||'')))put({id:'storage/resilience',label:'Storage resilience reduced',detail:b.degraded+' degraded or faulted Longhorn volumes',level:2,kind:'backup'});
  const wans=dailyWan(r=>list('WN'+r));
  if(wans.every(w=>w.status==='unknown'))put({id:'coverage/wan',label:'WAN observations unavailable',detail:'Both expected paths are unknown',level:2,unknown:true,kind:'wan'});
  for(const wan of wans.filter(w=>w.status!=='online'&&!wans.every(w=>w.status==='unknown')))put({id:'wan/'+wan.name,label:wan.name+' '+wan.status,detail:wan.status==='unknown'?'No fresh gateway observation':'Gateway reports this path offline',level:wans.every(w=>w.status==='offline')?3:2,unknown:wan.status==='unknown',kind:'wan'});
  const fresh=get('MV')===1&&get('MA')!=null&&get('MA')<=90;
  const amFresh=get('MU')===1&&get('MT')!=null&&get('MT')<=90;
  if(!fresh)put({id:'coverage/rules',label:'Rule coverage unknown',detail:'Cannot confirm quiet without current evaluators',level:2,unknown:true,kind:'monitor'});
  if(!amFresh)put({id:'coverage/delivery',label:'Notification coverage unknown',detail:'No fresh Alertmanager scrape',level:2,unknown:true,kind:'monitor'});
  for(const [ref,id,label] of [['ME','rule-errors','Rule evaluation errors'],['MN','notification-errors','Notification failures']])if(get(ref)>0)put({id:'monitor/'+id,label,detail:formatNumber(get(ref),0)+' in 24 h',level:2,kind:'monitor'});
  if(get('MH')===0)put({id:'monitor/heartbeat',label:'Watchdog deliveries stopped',detail:'No successful webhook acknowledgement in 10 min',level:2,kind:'monitor'});
  if(get('MD')>0&&!list('A').some(s=>s.labels.what==='Collectors silent'))put({id:'monitor/scrapes',label:'Scrapes failing',detail:get('MD')+' targets',level:2,kind:'monitor'});
  const missingMonitoring=['MD','ME','MN','MH'].filter(r=>get(r)==null);
  if(missingMonitoring.length)put({id:'coverage/monitoring',label:'Monitoring evidence incomplete',detail:missingMonitoring.length+' expected signals missing',level:2,unknown:true,kind:'monitor'});
  const recovered=[];
  for(const p of checks.filter(p=>p.status==='passing')){
    const h=seriesOf(data,'PH').find(s=>s.labels.probe===p.id),episodes=dailyEpisodes(h,from,to);if(!episodes.length)continue;
    const latest=episodes.at(-1),ah=history.filter(s=>s.labels.probe===p.id);
    const level=ah.some(s=>s.labels.severity==='critical'&&s.points.some(([t,v])=>v===1&&t>=latest.start&&t<=latest.end+120000))?3:ah.length?2:0;
    recovered.push({id:'probe/'+p.id,label:p.name,kind:'probe',probe:p.id,level,episodes:episodes.length,...latest});
  }
  const activeIds=new Set(raw.map(s=>dailyIdentity(s.labels)));
  const recoveredIds=new Set(recovered.map(s=>s.id));
  for(const h of [...history].sort((a,b)=>(b.labels.severity==='critical'?3:2)-(a.labels.severity==='critical'?3:2))){
    const id=dailyIdentity(h.labels);if(activeIds.has(id)||recoveredIds.has(id)||h.labels.probe||!fresh)continue;
    const points=h.points.filter(p=>p[1]===1);if(!points.length)continue;
    recoveredIds.add(id);const step=h.step||smallestStep(points)||60000,runs=[];
    for(const [t] of points){const prev=runs.at(-1);if(prev&&t-prev.end<=step*1.5)prev.end=t;else runs.push({start:t-step,end:t});}
    const last=runs.at(-1);
    recovered.push({id,label:human(h.labels.alertname),detail:[h.labels.source_namespace,h.labels.source,h.labels.instance,h.labels.path].filter(Boolean).join('/'),kind:'rule',level:h.labels.severity==='critical'?3:2,start:Math.max(from,last.start),end:last.end,duration:last.end-last.start,clipped:last.start<=from,uncertain:true,episodes:runs.length});
  }
  const all=[...map.values()];
  const action=all.filter(s=>s.level>=2).sort((a,b)=>b.level-a.level||Number(b.unknown)-Number(a.unknown)||a.label.localeCompare(b.label));
  const maintenance=all.filter(s=>s.level<2&&s.maintenance),observe=all.filter(s=>s.level<2&&!s.maintenance);
  recovered.sort((a,b)=>b.level-a.level||b.end-a.end);
  return {action,recovered,observe,maintenance,confirmed:fresh&&amFresh&&!action.some(s=>s.unknown),checks,workloads:w,backup:b,wans};
};
