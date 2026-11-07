#!/usr/bin/env python3
"""Run the same off-host native-launch contract in two existing SDK environments."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path('/tmp/glm1104-rebased-sdk-repository'))
    parser.add_argument('--repo-revision', default='b126d660', help='Input provenance label; file hashes verify content, never Git HEAD')
    parser.add_argument('--native-source', type=Path, default=Path('/tmp/glm1104-video15-locked-source'))
    parser.add_argument('--python-0310', type=Path, default=Path('/tmp/glm1104-qualification-venv/bin/python'))
    parser.add_argument('--python-042', type=Path, default=Path('/tmp/glm1104-sdk042-tests/bin/python'))
    parser.add_argument('--output', type=Path, default=Path(__file__).with_name('receipt.json'))
    args = parser.parse_args()
    worker = Path(__file__).with_name('cases.py').resolve()
    reports = []
    # These are the only child processes the runner starts. The worker installs
    # its process/network audit trap before importing SparkRun or the adapter.
    with tempfile.TemporaryDirectory(prefix='glm-sdk-compat-') as scratch:
        for version, interpreter in [('0.3.10', args.python_0310), ('0.4.2', args.python_042)]:
            report_path = Path(scratch) / (version + '.json')
            command = [str(interpreter.absolute()), '-I', '-B', str(worker),
                       '--repo', str(args.repo.resolve()), '--native-source', str(args.native_source.resolve()),
                       '--expected-sdk', version, '--report', str(report_path)]
            completed = subprocess.run(command, text=True, capture_output=True, timeout=60)
            if report_path.exists():
                report = json.loads(report_path.read_text())
            else:
                report = {'sdk_version': version, 'passed': False, 'error': completed.stderr or completed.stdout}
            report['worker_command'] = command[:-1] + ['<temporary-report>']
            report['worker_returncode'] = completed.returncode
            report['passed'] = report['passed'] and completed.returncode == 0
            reports.append(report)
            print(f"SparkRun {version}: {'PASS' if report['passed'] else 'FAIL'} ({report.get('tests_run', 0)} tests)")
            if not report['passed']:
                print(report.get('test_output', report.get('error', '')))
                if report.get('missing_required_calls'):
                    print('Required code not exercised:', report['missing_required_calls'])
                print(completed.stderr)
    receipt = {
        'schema': 1,
        'scope': 'Off-host SDK launch and hook compatibility; synthetic host observations and HTTP responses',
        'repository': str(args.repo.resolve()),
        'repository_revision_label': args.repo_revision,
        'revision_verification': 'Content SHA-256 only; Git HEAD is neither read nor modified',
        'native_source': str(args.native_source.resolve()),
        'harness_sha256': {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in (Path(__file__), worker)},
        'production_baseline': 'SparkRun 0.4.2 with the Python durable readiness-v2 profile, per merged PR #1302',
        'sdk_042_status': 'Promoted independently in PR #1302; these tests perform no host operation or deployment',
        'limitations': [
            'RuntimePlugin.run and the installed native orchestrator execute; launch_inference preflight, scheduling, image/model distribution and mod installation do not.',
            'The actual wait_for_port algorithm and socket-table command generator execute; their remote commands receive recorded synthetic results, without running ss, awk, nc or a shell.',
            'The executor generates real container scripts; they are recorded, never executed. No CLI run/stop command (including stop --dry-run) is invoked.',
            'The real Python capacity/readiness hook mains execute in process with a synthetic container log and HTTP responses. This cannot establish real GPU, NCCL, RoCE, capacity or inference readiness.',
            'Native flags and environment are checked against hashed source declarations/consumers. No Zig parser, native binary, model or native media path executes.',
            'Endpoint readiness is exercised through the real fallback path, with the Docker startup observer disabled at its observation boundary.',
            'The merged 0.4.2-specific placement/complete_post_launch suite is separate; these focused tests do not assert full-suite compatibility with 0.3.10.',
        ],
        'sdks': reports,
        'passed': all(report['passed'] for report in reports),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + '\n')
    print(f'Receipt: {args.output.resolve()}')
    raise SystemExit(0 if receipt['passed'] else 1)


if __name__ == '__main__':
    main()
