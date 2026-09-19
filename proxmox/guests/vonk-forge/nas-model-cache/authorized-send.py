#!/usr/bin/env python3
"""Forced SSH command: expose only hashes in a root-owned import plan."""
import json
import math
import os
from pathlib import Path
import re


def command(request, plan, directory):
    if len(request) > 1024:
        raise ValueError('Request too large')
    value = json.loads(request)
    if not isinstance(value, dict) or set(value) != {'sha256', 'max_mbps'}:
        raise ValueError('Expected only sha256 and max_mbps')
    sha, rate = value['sha256'], value['max_mbps']
    if not isinstance(sha, str) or re.fullmatch('[0-9a-f]{64}', sha) is None:
        raise ValueError('Invalid object hash')
    if type(rate) not in (float, int) or not math.isfinite(rate) or not 1 <= rate <= 80:
        raise ValueError('Rate must be between 1 and 80 MB/s')
    item = plan[sha]
    return ['sudo', '-n', 'python3', str(directory/'stream-object.py'), 'send',
            '--source', item['path'], '--source-root', item['root'],
            '--sha256', sha, '--bytes', str(item['bytes']), '--max-mbps', str(rate)]


def main():
    directory = Path(__file__).resolve().parent
    plan = json.loads((directory/'allowed-objects.json').read_text())
    args = command(os.environ.get('SSH_ORIGINAL_COMMAND', ''), plan, directory)
    os.execvp(args[0], args)


if __name__ == '__main__':
    main()
