#!/usr/bin/env python3
"""Require the production live-pool floor and agreed native device prompt-cache receipt.

The native /health route does not publish pool or prompt-cache capacity. Read the startup receipt instead;
the engine agrees the per-rank prompt-cache budget before printing it. Missing receipts fail closed.
This is a startup capacity gate only; it cannot establish durable reuse or full-profile qualification.
"""
import argparse
from pathlib import Path
import re


def check(log):
    pool = re.findall(r'^\[tensorfold\] CUDA .*taking (\d+) pages of 512 rows', log, re.M)
    cache = re.findall(r'^info: prompt cache: ([\d.]+) GiB of device pages and ([\d.]+) GiB of pinned host memory a rank', log, re.M)
    if len(pool) != 1 or len(cache) != 1:
        raise ValueError('Missing or ambiguous native GLM startup capacity receipts')
    tokens = int(pool[0]) * 512
    if tokens < 2582528:
        raise ValueError(f'Insufficient GLM pool: {tokens} tokens')
    if float(cache[0][0]) <= 0 or float(cache[0][1]) != 0:
        raise ValueError(f'Expected nonzero device prompt cache and no pinned-host tier: {cache[0]}')
    return tokens, float(cache[0][0])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--log', default='/tmp/sparkrun_serve.log')
    args = parser.parse_args()
    tokens, cache = check(Path(args.log).read_text())
    print(f'GLM capacity passed: {tokens} pool tokens, {cache} GiB device prompt cache per rank')


if __name__ == '__main__':
    main()
