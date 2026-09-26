#!/usr/bin/env python3
"""Countable ordinary JSON workload for delivery tests on stock collectors."""
import datetime as dt
import json
import os
import sys
import time

run = os.environ.get('FIXTURE_RUN', 'vlagent-583')
count = int(os.environ.get('FIXTURE_COUNT', '3600'))
time.sleep(10)  # allow discovery before the first event
for n in range(count):
    emitted = dt.datetime.now(dt.timezone.utc).isoformat()
    row = {'fixture_run': run, 'n': n, 'level': 'WaRn' if n % 10 == 0 else 'INFO',
           'message': 'fixture ' + ('x' * 40000 if n % 100 == 0 else str(n)) + ' end',
           'time': emitted, 'emitted_at': emitted}
    print(json.dumps(row), file=sys.stderr if n % 2 else sys.stdout, flush=True)
    time.sleep(0.05)
time.sleep(600)  # retain pod metadata/logs during recovery
