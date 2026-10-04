#!/usr/bin/env python3
"""Behavioral host-health fixtures; only enrollment varies from production rules."""
import copy
import json
import pathlib
import sys

OUT, ROOT = map(pathlib.Path, sys.argv[1:])
rules = json.loads((OUT / 'source.json').read_text())
alerts = {r['alert']: r for g in rules['groups'] for r in g['rules'] if 'alert' in r}
for name, enrollment in [('disabled', 'vector(0)'), ('enrolled', 'vector(1)')]:
    variant = copy.deepcopy(rules)
    for group in variant['groups']:
        if group['name'] == 'external-host-inventory':
            for rule in group['rules']:
                rule['expr'] = enrollment
    (OUT / f'rules-{name}.json').write_text(json.dumps(variant, indent=2) + '\n')

HOSTS = [('spark-1', 'dgx_spark'), ('spark-2', 'dgx_spark'), ('pve-sbx-1', 'proxmox'), ('pve-sbx-2', 'proxmox'), ('pve-sbx-3', 'proxmox')]
LENGTH = 90
BASE = {}
def item(host, metric, job, values='1+0x90', **extra):
    platform = dict(HOSTS)[host]
    labels = dict(job=job, instance=host, host=host, platform=platform, collector='spark-host-vmagent' if platform == 'dgx_spark' else 'pve-host-vmagent', **extra)
    series = metric + '{' + ','.join(k+'='+json.dumps(v) for k,v in labels.items()) + '}'
    return dict(series=series, values=values)
def put(host, metric, job, values='1+0x90', **extra):
    data = item(host, metric, job, values, **extra)
    BASE[(host,metric,job,tuple(extra.items()))] = data
for host, platform in HOSTS:
    node = 'spark-node' if platform == 'dgx_spark' else 'pve-node'
    collector = 'spark-collector' if platform == 'dgx_spark' else 'pve-vmagent'
    put(host, 'up', node)
    put(host, 'up', collector)
    put(host, 'vm_promscrape_config_last_reload_successful', collector)
    put(host, 'vmagent_remotewrite_pending_data_bytes', collector, '0+0x90', path='/queue/persistent-queue/1_fixture', url='1:secret-url')
    put(host, 'vm_persistentqueue_bytes_dropped_total', collector, '0+0x90', path='/queue/persistent-queue/1_fixture', name='1:secret-url')
    if platform == 'dgx_spark':
        put(host, 'up', 'spark-gpu')
        put(host, 'nvidia_smi_last_collect_success', 'spark-gpu')
        put(host, 'nvidia_smi_last_collect_success_timestamp_seconds', 'spark-gpu', '0+30x90')
        put(host, 'nvidia_smi_clocks_event_reasons_hw_thermal_slowdown', 'spark-gpu', '0+0x90', uuid='gpu-'+host)
        put(host, 'nvidia_smi_clocks_event_reasons_sw_thermal_slowdown', 'spark-gpu', '0+0x90', uuid='gpu-'+host)
        put(host, 'spark_monitoring_config_success', 'spark-node')
        put(host, 'up', 'spark-deployer')
        # Real exporter CounterVec has repository; up does not.
        put(host, 'doco_cd_polls_total', 'spark-deployer', '0+1x90', repository='https://github.com/kelchm/home-lab.git')
    else:
        put(host, 'pve_hostcfg_dirty', 'pve-node', '0+0x90')
        put(host, 'pve_hostcfg_fetch_success', 'pve-node')
        put(host, 'pve_hostcfg_attempt_success', 'pve-node')
        put(host, 'pve_hostcfg_drift', 'pve-node', '0+0x90')
        put(host, 'pve_hostcfg_status_timestamp_seconds', 'pve-node', '0+30x90')

def changed(overrides=(), remove=()):
    """Apply isolated faults to a healthy fleet baseline."""
    data = dict(BASE)
    for host, metric, job in remove:
        data = {k:v for k,v in data.items() if k[:3] != (host,metric,job)}
    for host, metric, job, values in overrides:
        found = [k for k in data if k[:3] == (host,metric,job)]
        assert len(found) == 1, (host,metric,job,found)
        key = found[0]
        data[key] = dict(data[key], values=values)
    return list(data.values())
def expected(name, hosts):
    rule=alerts[name]
    return [dict(exp_labels=dict(host=h, platform=dict(HOSTS)[h], **rule['labels']), exp_annotations={k:v.replace('{{ $labels.host }}',h) for k,v in rule['annotations'].items()}) for h in hosts]
def check(name, at='6m', hosts=()):
    return dict(eval_time=at, alertname=name, exp_alerts=expected(name,hosts))
def expr(query, at, samples):
    return dict(expr=query, eval_time=at, exp_samples=[dict(labels=labels,value=value) for labels,value in samples])
def record_sample(name, host='spark-1', value=1):
    return (f'{name}{{host="{host}",platform="{dict(HOSTS)[host]}"}}', value)
def case(name, series=None, checks=(), expressions=()):
    result = dict(name=name, interval='30s', input_series=series if series is not None else list(BASE.values()))
    if checks: result['alert_rule_test']=list(checks)
    if expressions: result['promql_expr_test']=list(expressions)
    return result

N='ExternalHostMetricsMissing'; C='ExternalHostCollectorMissing'; G='SparkGpuCollectionFailed'; T='SparkGpuThermalThrottling'; B='ExternalHostQueueBacklog'; D='ExternalHostQueueDataDropped'; R='ExternalHostScrapeConfigRejected'; P='PveHostConfigurationUnhealthy'; S='PveHostConfigurationStatusStale'; L='SparkMonitoringPollStalled'
tests=[]
tests.append(case('Healthy enrolled fleet; real repository labels do not cause polling pages',checks=[check(name,'30m') for name in alerts],expressions=[expr('external_host:node_fresh', '3m', [record_sample('external_host:node_fresh',h) for h,_ in HOSTS]),expr('external_host:gpu_fresh','3m',[record_sample('external_host:gpu_fresh',h) for h in ['spark-1','spark-2']])]))
for label, overrides, removal, at in [
    ('absent', [], [('spark-1','up','spark-node')], '6m'),
    ('down', [('spark-1','up','spark-node','0+0x90')], [], '6m'),
    ('old replay', [('spark-1','up','spark-node','1 1 1 _x88')], [], '8m'),
]:
    tests.append(case('Node '+label+' pages once and suppresses downstream failures',changed(overrides,removal),[check(N,at,['spark-1'])]+[check(a,at) for a in [C,G,T,B,D,R,L]], [expr('external_host:node_fresh{host="spark-1"}','3m',[])]))
tests.append(case('Node sample expires exactly at 90 seconds while still within PromQL lookback',changed([('spark-1','up','spark-node','1 1 1 _x88')]),expressions=[expr('external_host:node_fresh{host="spark-1"}','2m',[record_sample('external_host:node_fresh')]),expr('external_host:node_fresh{host="spark-1"}','2m30s',[])]))
tests.append(case('Recovered node clears the missing alert and becomes fresh',changed([('spark-1','up','spark-node','0+0x12 1+0x77')]),[check(N,'6m',['spark-1']),check(N,'7m')],[expr('external_host:node_fresh{host="spark-1"}','7m',[record_sample('external_host:node_fresh')])]))
for label, overrides, removal, at in [
    ('absent',[],[('spark-1','up','spark-collector')],'6m'),
    ('down',[('spark-1','up','spark-collector','0+0x90')],[],'6m'),
    ('old',[('spark-1','up','spark-collector','1 1 1 _x88')],[],'8m'),
]:
    tests.append(case('Collector self scrape '+label+' with a fresh node',changed(overrides,removal),[check(C,at,['spark-1']),check(N,at),check(B,at),check(D,at)]))
tests.append(case('HTTP 200 cached GPU data expires by successful command age, thermal suppressed',changed([('spark-1','nvidia_smi_last_collect_success_timestamp_seconds','spark-gpu','0+0x90'),('spark-1','nvidia_smi_clocks_event_reasons_sw_thermal_slowdown','spark-gpu','1+0x90')]),[check(G,'3m',['spark-1']),check(T,'6m')],[expr('external_host:gpu_fresh{host="spark-1"}','3m',[])]))
tests.append(case('GPU command age is strictly less than 20 seconds',changed([('spark-1','nvidia_smi_last_collect_success_timestamp_seconds','spark-gpu','-20+30x90')]),[check(G,'3m',['spark-1'])],[expr('external_host:gpu_fresh{host="spark-1"}','30s',[])]))
for label, overrides, removal in [
    ('target absent',[],[('spark-1','up','spark-gpu')]),
    ('target down',[('spark-1','up','spark-gpu','0+0x90')],[]),
    ('command failed',[('spark-1','nvidia_smi_last_collect_success','spark-gpu','0+0x90')],[]),
    ('success timestamp absent',[],[('spark-1','nvidia_smi_last_collect_success_timestamp_seconds','spark-gpu')]),
]:
    tests.append(case('GPU '+label+' while node remains fresh',changed(overrides,removal),[check(G,'3m',['spark-1']),check(N,'6m')]))
tests.append(case('GPU fresh software thermal flag alone is sufficient; hardware flag stays zero',changed([('spark-1','nvidia_smi_clocks_event_reasons_sw_thermal_slowdown','spark-gpu','1+0x90')]),[check(T,'4m'),check(T,'6m',['spark-1']),check(G,'6m')]))
tests.append(case('GPU recovery clears command failure',changed([('spark-1','nvidia_smi_last_collect_success','spark-gpu','0+0x6 1+0x83')]),[check(G,'3m',['spark-1']),check(G,'4m')],[expr('external_host:gpu_fresh{host="spark-1"}','4m',[record_sample('external_host:gpu_fresh')])]))
tests.append(case('Continuous queue backlog waits fifteen minutes then resolves on drain',changed([('spark-1','vmagent_remotewrite_pending_data_bytes','spark-collector','64+0x32 0+0x57')]),[check(B,'14m'),check(B,'16m',['spark-1']),check(B,'17m')]))
tests.append(case('Queue bytes discarded warn immediately then age out of the fifteen-minute window',changed([('spark-1','vm_persistentqueue_bytes_dropped_total','spark-collector','0 100+0x89')]),[check(D,'2m',['spark-1']),check(D,'18m')]))
for label, metric, job in [('publisher','spark_monitoring_config_success','spark-node'),('runtime reload','vm_promscrape_config_last_reload_successful','spark-collector')]:
    tests.append(case('Rejected '+label+' warns even though the prior configuration is collecting',changed([('spark-1',metric,job,'0+0x90')]),[check(R,'4m'),check(R,'6m',['spark-1']),check(N,'6m')]))
for label, metric, value in [('detected drift','pve_hostcfg_drift','1'),('unknown drift check','pve_hostcfg_drift','-1'),('interrupted apply','pve_hostcfg_dirty','1'),('fetch failure','pve_hostcfg_fetch_success','0'),('apply failure','pve_hostcfg_attempt_success','0')]:
    tests.append(case('PVE '+label+' with fresh node and status',changed([('pve-sbx-1',metric,'pve-node',value+'+0x90')]),[check(P,'14m'),check(P,'16m',['pve-sbx-1']),check(S,'16m')]))
tests.append(case('PVE status absent while node is fresh',changed(remove=[('pve-sbx-1','pve_hostcfg_status_timestamp_seconds','pve-node')]),[check(S,'6m',['pve-sbx-1']),check(N,'6m')]))
tests.append(case('PVE status older than fifteen minutes warns after five more minutes',changed([('pve-sbx-1','pve_hostcfg_status_timestamp_seconds','pve-node','0+0x90')]),[check(S,'19m'),check(S,'21m',['pve-sbx-1'])]))
paused=changed()
paused.append(item('pve-sbx-1','pve_hostcfg_paused','pve-node'))
tests.append(case('PVE deliberate pause with reporting timer is not an unhealthy apply',paused,[check(P,'30m'),check(S,'30m')]))
for label, overrides, removal, at in [
    ('target absent',[],[('spark-1','up','spark-deployer')],'6m'),
    ('target down',[('spark-1','up','spark-deployer','0+0x90')],[],'6m'),
    ('target old',[('spark-1','up','spark-deployer','1 1 1 _x88')],[],'8m'),
    ('poll counter absent',[],[('spark-1','doco_cd_polls_total','spark-deployer')],'6m'),
    ('no successful polls',[('spark-1','doco_cd_polls_total','spark-deployer','0+0x90')],[],'6m'),
]:
    tests.append(case('Spark deployer '+label+' warns with fresh node',changed(overrides,removal),[check(L,at,['spark-1']),check(N,at)]))
tests.append(case('Spark deployer polling recovery clears alert despite repository-only labels',changed([('spark-1','up','spark-deployer','0+0x12 1+0x77'),('spark-1','doco_cd_polls_total','spark-deployer','0+0x12 1+1x77')]),[check(L,'6m',['spark-1']),check(L,'7m')]))

# The disabled variant must suppress every alert even with a live unhealthy host.
unhealthy=changed([('spark-1','nvidia_smi_clocks_event_reasons_sw_thermal_slowdown','spark-gpu','1+0x90'),('spark-1','vmagent_remotewrite_pending_data_bytes','spark-collector','64+0x90'),('spark-1','vm_persistentqueue_bytes_dropped_total','spark-collector','0+100x90'),('spark-1','spark_monitoring_config_success','spark-node','0+0x90'),('spark-1','up','spark-deployer','0+0x90'),('pve-sbx-1','pve_hostcfg_drift','pve-node','1+0x90')],remove=[('pve-sbx-1','pve_hostcfg_status_timestamp_seconds','pve-node'),('spark-2','up','spark-node'),('pve-sbx-2','up','pve-vmagent'),('spark-1','up','spark-gpu')])
disabled=[case('Prepared fleet without any samples does not page',[],[check(a,'30m') for a in alerts],[expr('external_host:expected','1m',[record_sample('external_host:expected',h,0) for h,_ in HOSTS])]),case('Unenrolled unhealthy fleet suppresses all host-specific alerts',unhealthy,[check(a,'30m') for a in alerts])]
for filename, variant, groups in [('test.json','enrolled',tests),('disabled-test.json','disabled',disabled)]:
    fixture=dict(rule_files=[f'rules-{variant}.json'],evaluation_interval='30s',tests=groups)
    (OUT/filename).write_text(json.dumps(fixture))

# Parse every real dashboard query and guard the identical-label "or" regression.
dashboard=json.loads((ROOT/'kubernetes/apps/observability/grafana/app/external-hosts-dashboard.json').read_text())
queries=[]
for panel in dashboard['panels']:
    for target in panel.get('targets',[]):
        queries.append(dict(record=f'dashboard_query_{len(queries)}',expr=target['expr'].replace('$host','.*')))
(OUT/'dashboard-rules.json').write_text(json.dumps(dict(groups=[dict(name='dashboard-query-parsing',rules=queries)])))
drift_panel=next(p for p in dashboard['panels'] if p['title']=='PVE drift / interrupted apply')
dirty=item('pve-sbx-1','pve_hostcfg_dirty','pve-node','0+0x5')
drift=item('pve-sbx-1','pve_hostcfg_drift','pve-node','1+0x5')
regression=case('Dashboard retains drift separately from clean interrupted-apply status',[dirty,drift],expressions=[expr(drift_panel['targets'][0]['expr'].replace('$host','pve-sbx-1'),'1m',[(dirty['series'],0),(drift['series'],1)])])
(OUT/'dashboard-test.json').write_text(json.dumps(dict(rule_files=['rules-enrolled.json'],evaluation_interval='30s',tests=[regression])))
print(f'Generated {len(tests)} enrolled, {len(disabled)} disabled and 1 dashboard regression scenario')
