#!/usr/bin/env python3
"""Offline-only full-object scope check. No HTTP, credentials or controller writes."""
import argparse
import copy
import json
from pathlib import Path

FORWARDS = {'6ab93666519d986cdf987240': '10.32.140.1',
            '6ad6c08f519d986cdf2ee284': '10.32.130.1'}
REMOTE = '6a97a4d7a6bd0ee70a238923'
INTERNAL = '677f0352a46263726567a989'
OLD = ['10.32.19.103']
NEW = OLD + ['10.32.19.104']


def require(condition, message):
    if not condition:
        raise ValueError(message)


def objects(snapshot):
    if isinstance(snapshot, dict):
        snapshot = snapshot['netbird_policies']
    require(isinstance(snapshot, list), 'Expected a policy list or selected readback')
    result = {}
    for obj in snapshot:
        ident = obj['_id']
        require(ident not in result, 'Duplicate policy ID')
        result[ident] = obj
    return result


def semantic(obj):
    # These two top-level counters may change during the edit. Everything else is compared.
    return {k: v for k, v in obj.items() if k not in {'hits', 'last_hit'}}


def validate_pair(forward, reply, vip, sources):
    require(forward['action'] == reply['action'] == 'ALLOW', 'Expected ALLOW')
    require(forward['enabled'] is True and reply['enabled'] is True, 'Disabled policy')
    require(forward['protocol'] == reply['protocol'] == 'tcp', 'Protocol drift')
    require(forward['ip_version'] == reply['ip_version'] == 'IPV4', 'IP version drift')
    require(forward['create_allow_respond'] is True, 'Generated response disabled')
    require(reply['origin_id'] == forward['_id'], 'Wrong generated origin')
    require(reply['connection_state_type'] == 'RESPOND_ONLY' and
            set(reply['connection_states']) == {'RELATED', 'ESTABLISHED'}, 'Return state drift')
    require(forward['connection_state_type'] == 'ALL' and not forward['connection_states'],
            'Forward state drift')
    require(forward['schedule'] == {'mode': 'ALWAYS'}, 'Schedule drift')
    for endpoint, ips, zone, port in [
        (forward['source'], sources, REMOTE, None),
        (forward['destination'], [vip], INTERNAL, '443'),
        (reply['source'], [vip], INTERNAL, '443'),
        (reply['destination'], sources, REMOTE, None),
    ]:
        require(endpoint['ips'] == ips and endpoint['zone_id'] == zone, 'Address/zone drift')
        require(endpoint['matching_target'] == 'IP' and
                endpoint['matching_target_type'] == 'SPECIFIC', 'Target drift')
        require(not endpoint['match_opposite_ips'] and not endpoint['match_opposite_ports'],
                'Inverted match')
        if port is not None:
            require(endpoint['port'] == port and endpoint['port_matching_type'] == 'SPECIFIC',
                    'Port drift')
        else:
            require(endpoint['port_matching_type'] == 'ANY' and not endpoint.get('match_mac'),
                    'Source/return scope drift')
    require(not forward.get('match_opposite_protocol') and not reply.get('match_opposite_protocol'),
            'Inverted protocol')


def prepare(snapshot):
    before = objects(snapshot)
    changes = []
    for ident, vip in FORWARDS.items():
        forward = before[ident]
        replies = [p for p in before.values() if p.get('origin_id') == ident]
        require(len(replies) == 1, 'Expected exactly one generated return by origin ID')
        reply = replies[0]
        validate_pair(forward, reply, vip, OLD)
        proposed = copy.deepcopy(forward)
        proposed['source']['ips'] = NEW[:]
        changes.append(proposed)
    return changes


def verify(before_snapshot, after_snapshot):
    before, after = objects(before_snapshot), objects(after_snapshot)
    proposed = {p['_id']: p for p in prepare(before_snapshot)}
    for ident, vip in FORWARDS.items():
        old_return = next(p for p in before.values() if p.get('origin_id') == ident)
        reply = after[old_return['_id']]
        require(len([p for p in after.values() if p.get('origin_id') == ident]) == 1,
                'Duplicate generated return')
        validate_pair(after[ident], reply, vip, NEW)
        expected_return = copy.deepcopy(old_return)
        expected_return['destination']['ips'] = NEW[:]
        require(semantic(after[ident]) == semantic(proposed[ident]),
                'Forward changed beyond source.ips; refresh/review instead of overwriting')
        require(semantic(reply) == semantic(expected_return),
                'Generated return changed beyond destination.ips')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['prepare', 'verify'])
    parser.add_argument('before', type=Path)
    parser.add_argument('after', type=Path, nargs='?')
    args = parser.parse_args()
    before = json.loads(args.before.read_text())
    if args.mode == 'prepare':
        require(args.after is None, 'prepare takes one snapshot')
        print(json.dumps(prepare(before), indent=2))
    else:
        require(args.after is not None, 'verify requires after snapshot')
        verify(before, json.loads(args.after.read_text()))
        print('Both forward changes and generated returns match the reviewed source-only delta.')


if __name__ == '__main__':
    main()
