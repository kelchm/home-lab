#!/usr/bin/env python3
"""Check startup receipts; this is admission evidence, not durable qualification."""
import argparse
import json
from pathlib import Path
import re
import urllib.request


def check_health(body):
    """Check the native engine's actual sharded allocation, independent of planner receipts."""
    if not isinstance(body, dict) or body.get('status') != 'ok' or body.get('warming') is not False:
        raise ValueError('Native engine is unhealthy or warming')
    if body.get('model') != 'GLM-5.3-Flash' or body.get('max_batch_size') != 4:
        raise ValueError('Unexpected native model or loaded streams')
    shard = body.get('kv_shard')
    if not isinstance(shard, dict) or type(shard.get('parts')) is not int or shard['parts'] != 2 or shard.get('page_tokens') != 512:
        raise ValueError('Expected two-part FP8 KV shard allocation in /health')
    pages = shard.get('part_pages')
    if type(pages) is not int or pages < 2049:
        raise ValueError('Actual KV shard part cannot hold 1M plus verification room')
    parts = shard.get('per_part')
    if not isinstance(parts, list) or len(parts) != 2 or any(not isinstance(part, dict) for part in parts):
        raise ValueError('Missing actual per-part KV ownership receipts')
    return pages


def check(log, health, min_pool=2582528, min_cache=2.0, learn_gib=64, learn_dir=None):
    actual_part_pages = check_health(health)
    pool = re.findall(r'^\[tensorfold\] CUDA .*taking (\d+) pages of 512 rows', log, re.M)
    cache = re.findall(r'^info: prompt cache: ([\d.]+) GiB of device pages and ([\d.]+) GiB of pinned host memory a rank', log, re.M)
    if len(pool) != 1 or len(cache) != 1:
        raise ValueError('Missing or ambiguous native startup receipts')
    pages = int(pool[0])
    # Geometry.half in this released kit reserves four planner pages before splitting.
    part_pages = max(pages - 4, 0) // 2
    # A 1M cap also reserves the native verification window. Each sharded part must fit it.
    required_pages = (1048576 + 16 + 511) // 512
    device, host = map(float, cache[0])
    usable_tokens = 2 * part_pages * 512
    if usable_tokens < min_pool or 2 * actual_part_pages * 512 < min_pool or part_pages < required_pages:
        raise ValueError('Insufficient aggregate pool or sharded part for the 1M profile')
    if device < min_cache or host != 0:
        raise ValueError('Insufficient funded device cache or unexpected host tier')
    learned = re.findall(r'^info: glm: conversation states on disk in (.+) \((\d+) states, (\d+) MiB of (\d+) MiB; (\d+) ranks\x27 parts\)', log, re.M)
    if len(learned) != 1 or int(learned[0][3]) != learn_gib * 1024 or int(learned[0][4]) != 2:
        raise ValueError('The learned store did not open with its recorded quota and both ranks')
    # The source's store identity is a 16-digit lowercase hex child of --learn-dir.
    # Never accept the root itself, a sibling, deeper paths or path normalization escapes.
    if learn_dir is None or re.fullmatch(re.escape(learn_dir.rstrip('/')) + r'/[0-9a-f]{16}', learned[0][0]) is None:
        raise ValueError('The learned store must open in one identity child of the recorded directory')
    return {'planner_pages': pages, 'aggregate_pool_tokens': usable_tokens,
            'minimum_part_pages': part_pages, 'actual_part_pages': actual_part_pages,
            'actual_aggregate_pool_tokens': 2 * actual_part_pages * 512, 'minimum_part_tokens': part_pages * 512,
            'device_cache_gib_per_rank': device, 'pinned_host_cache_gib_per_rank': host,
            'context': 1048576, 'streams': 4, 'learned_store': learned[0][0],
            'learned_store_location': 'rank0', 'learned_store_mib': int(learned[0][2]),
            'durability_status': 'unknown'}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--url', default='http://127.0.0.1:8888')
    p.add_argument('--log', default='/tmp/sparkrun_serve.log')
    p.add_argument('--learn-gib', type=int, choices=(1, 64), default=64)
    p.add_argument('--learn-dir', required=True)
    args = p.parse_args()
    with urllib.request.urlopen(args.url.rstrip('/') + '/health', timeout=30) as response:
        health = json.load(response)
    print(json.dumps(check(Path(args.log).read_text(), health, learn_gib=args.learn_gib, learn_dir=args.learn_dir), sort_keys=True))


if __name__ == '__main__':
    main()
