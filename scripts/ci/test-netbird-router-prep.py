#!/usr/bin/env python3
"""Offline fixtures for proposed router definition, snippet guards and firewall delta."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
GUEST = ROOT / 'proxmox/guests/netbird-router-2'
spec = importlib.util.spec_from_file_location('delta', GUEST / 'check-unifi-delta.py')
delta = importlib.util.module_from_spec(spec)
spec.loader.exec_module(delta)


class UniFi(unittest.TestCase):
    def setUp(self):
        self.before = json.loads((ROOT / 'scripts/ci/fixtures/netbird-unifi.json').read_text())

    def after(self):
        result = copy.deepcopy(self.before)
        proposed = {x['_id']: x for x in delta.prepare(self.before)}
        for p in result:
            if p['_id'] in proposed:
                p['source']['ips'] = delta.NEW[:]
            elif p.get('origin_id') in proposed:
                p['destination']['ips'] = delta.NEW[:]
        return result

    def test_only_two_forward_candidates_preserve_fresh_names(self):
        for p in self.before:
            p['name'] = p['name'].replace('Pilot ', '')
        candidates = delta.prepare(self.before)
        self.assertEqual({p['_id'] for p in candidates}, set(delta.FORWARDS))
        self.assertTrue(all('Pilot' not in p['name'] for p in candidates))
        delta.verify(self.before, self.after())

    def test_counters_can_change(self):
        after = self.after()
        for p in after:
            p.update(hits=999, last_hit=123)
        delta.verify(self.before, after)

    def test_reject_other_semantic_changes(self):
        for ident in [next(iter(delta.FORWARDS)), self.before[0]['_id']]:
            for key, value in [('name', 'stale overwrite'), ('unknown_flag', True),
                               ('index', 999), ('protocol', 'all')]:
                after = self.after()
                next(p for p in after if p['_id'] == ident)[key] = value
                with self.subTest(ident=ident, key=key), self.assertRaises(ValueError):
                    delta.verify(self.before, after)

    def test_full_inventory_rejects_added_removed_or_changed_unrelated_policy(self):
        unrelated = copy.deepcopy(self.before[0])
        unrelated.update(_id='unrelated', origin_id='other', enabled=False)
        self.before.append(unrelated)
        for change in ['add', 'remove', 'enable', 'unknown_field']:
            after = self.after()
            if change == 'add':
                broad = copy.deepcopy(unrelated)
                broad.update(_id='broad-104', enabled=True)
                broad['source']['ips'] = ['10.32.19.104']
                after.append(broad)
            elif change == 'remove':
                after.pop()
            elif change == 'enable':
                after[-1]['enabled'] = True
            else:
                after[-1]['unknown_flag'] = True
            with self.subTest(change=change), self.assertRaises(ValueError):
                delta.verify(self.before, after)

    def test_full_inventory_allows_unrelated_counters_and_list_reordering(self):
        unrelated = copy.deepcopy(self.before[0])
        unrelated.update(_id='unrelated', origin_id='other')
        self.before.append(unrelated)
        after = self.after()
        after[-1].update(hits=999, last_hit=123)
        delta.verify(self.before, list(reversed(after)))

    def test_reject_broad_baseline_or_missing_103(self):
        for ips in [['10.32.19.0/24'], ['10.32.19.104']]:
            before = copy.deepcopy(self.before)
            next(p for p in before if p['_id'] in delta.FORWARDS)['source']['ips'] = ips
            with self.subTest(ips=ips), self.assertRaises(ValueError):
                delta.prepare(before)

    def test_reject_wrong_destination_port_zone_or_return_origin(self):
        for field, value in [('ips', ['10.32.130.0/24']), ('port', 'ANY'), ('zone_id', 'other')]:
            before = copy.deepcopy(self.before)
            next(p for p in before if p['_id'] in delta.FORWARDS)['destination'][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                delta.prepare(before)
        before = copy.deepcopy(self.before)
        next(p for p in before if p.get('origin_id'))['origin_id'] = 'wrong'
        with self.assertRaises(ValueError):
            delta.prepare(before)

    def test_reject_duplicate_or_unregenerated_return(self):
        with self.assertRaises(ValueError):
            delta.prepare(self.before + self.before[:1])
        after = self.after()
        next(p for p in after if p.get('origin_id'))['destination']['ips'] = delta.OLD[:]
        with self.assertRaises(ValueError):
            delta.verify(self.before, after)


class Snippet(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='netbird-snippet-test-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.local = self.base / 'local'
        self.local.mkdir()
        self.evidence = self.base / 'root'
        self.evidence.mkdir()
        self.bin = self.base / 'bin'
        self.bin.mkdir()
        self.env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ['PATH'],
                        FIXTURE=str(self.base))
        # Test a disposable copy with only the two absolute roots relocated.
        # Production has no environment override that can redirect its destination.
        source = (GUEST / 'provision-snippet.sh').read_text()
        source = source.replace('/var/lib/vz', str(self.local)).replace('/root/', str(self.evidence) + '/')
        self.script = self.base / 'provision.sh'
        self.script.write_text(source)
        self.source = self.base / 'vendor.yaml'
        self.source.write_bytes(b'credential-free fixture\n')
        self.sha = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.storage = dict(type='dir', path=str(self.local), content='iso,backup,import,vztmpl',
                            digest='original', arbitrary='preserve-me')
        self.save_storage()
        self.write('sha256sum', '''#!/usr/bin/env python3
import hashlib, pathlib, sys
assert sys.argv[1:]==['--check','--status']
sha, path=sys.stdin.read().strip().split('  ',1)
sys.exit(0 if hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()==sha else 1)
''')
        self.write('pvesh', '''#!/usr/bin/env python3
import json, os, pathlib, sys
assert sys.argv[1:] == ['get','/storage/local','--output-format','json']
p=pathlib.Path(os.environ['FIXTURE']) / 'storage.json'
print(p.read_text())
''')
        self.write('pvesm', '''#!/usr/bin/env python3
import json, os, pathlib, sys
base=pathlib.Path(os.environ['FIXTURE']);p=base/'storage.json'
if sys.argv[1:3] == ['set','local']:
    assert sys.argv[3:5] == ['--digest','original']
    assert sys.argv[5] == '--content'
    s=json.loads(p.read_text());assert s['digest']=='original'
    s['content']=sys.argv[6];s['digest']='updated';p.write_text(json.dumps(s))
    (base/'mutated').touch()
else:
    assert sys.argv[1:] == ['path','local:snippets/netbird-router-2.yaml']
    print(base/'local/snippets/netbird-router-2.yaml')
''')
        # Model Linux exclusive hard-link publication, using the host's OS primitive.
        self.write('ln', '''#!/usr/bin/env python3
import os, pathlib, sys
assert sys.argv[1:3]==['-T','--']
if os.environ.get('RACE'):
    pathlib.Path(sys.argv[4]).write_text('concurrent file')
os.link(sys.argv[3],sys.argv[4])
''')
        self.write('install', '''#!/usr/bin/env python3
import os, pathlib, shutil, sys
args=sys.argv[1:]
assert args[:1] in [['-d'], ['-o']]
if args[0]=='-d':
    assert args[1:7]==['-o','root','-g','root','-m','0755']
    pathlib.Path(args[7]).mkdir();os.chmod(args[7],0o755)
else:
    assert args[:6]==['-o','root','-g','root','-m','0644']
    shutil.copyfile(args[6],args[7]);os.chmod(args[7],0o644)
''')
        self.write('stat', '''#!/usr/bin/env python3
import pathlib, stat, sys
assert sys.argv[1:3]==['-c','%U:%G %a']
assert stat.S_IMODE(pathlib.Path(sys.argv[3]).stat().st_mode)==0o644
# Real root ownership is a live readback gate; fixture files stay owned by the test user.
print('root:root 644')
''')

    def write(self, name, text):
        p = self.bin / name
        p.write_text(text)
        p.chmod(0o755)

    def save_storage(self):
        (self.base / 'storage.json').write_text(json.dumps(self.storage))

    def run_script(self, sha=None):
        return subprocess.run(['bash', str(self.script), str(self.source), 'netbird-router-2.yaml', sha or self.sha],
                              env=self.env, capture_output=True, text=True, timeout=10)

    @property
    def destination(self):
        return self.local / 'snippets/netbird-router-2.yaml'

    def test_install_preserves_flags_and_unknown_fields(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.destination.read_bytes(), self.source.read_bytes())
        after = json.loads((self.base / 'storage.json').read_text())
        self.assertEqual(set(after['content'].split(',')), set(self.storage['content'].split(',')) | {'snippets'})
        self.assertEqual(after['arbitrary'], self.storage['arbitrary'])
        self.assertEqual(list(self.destination.parent.glob('.netbird-vendor.*')), [])

    def test_existing_matching_file_and_reordered_flags_no_rewrite(self):
        self.storage['content'] = 'snippets,vztmpl,import,backup,iso'
        self.save_storage()
        self.destination.parent.mkdir()
        self.destination.write_bytes(self.source.read_bytes())
        self.destination.chmod(0o644)
        before = self.destination.stat()
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        after = self.destination.stat()
        self.assertEqual((before.st_ino, before.st_mtime_ns), (after.st_ino, after.st_mtime_ns))
        self.assertFalse((self.base / 'mutated').exists())

    def test_refuse_mismatch_or_dangling_symlink(self):
        self.destination.parent.mkdir()
        self.destination.write_text('existing different file')
        self.assertNotEqual(self.run_script().returncode, 0)
        self.assertEqual(self.destination.read_text(), 'existing different file')
        self.destination.unlink()
        self.destination.symlink_to(self.base / 'absent')
        self.assertNotEqual(self.run_script().returncode, 0)
        self.assertTrue(self.destination.is_symlink())
        self.assertFalse((self.base / 'absent').exists())

    def test_refuse_symlink_directory_or_source(self):
        target = self.base / 'elsewhere'
        target.mkdir()
        (self.local / 'snippets').symlink_to(target, target_is_directory=True)
        self.assertNotEqual(self.run_script().returncode, 0)
        self.assertEqual(list(target.iterdir()), [])
        (self.local / 'snippets').unlink()
        original = self.base / 'original'
        self.source.rename(original)
        self.source.symlink_to(original)
        self.assertNotEqual(self.run_script().returncode, 0)
        self.assertFalse(self.destination.exists())

    def test_wrong_sha_and_storage_layout_fail_closed(self):
        self.assertNotEqual(self.run_script(sha='0' * 64).returncode, 0)
        self.assertFalse((self.base / 'mutated').exists())
        self.storage['shared'] = 1
        self.save_storage()
        self.assertNotEqual(self.run_script().returncode, 0)
        self.assertFalse(self.destination.exists())

    def test_concurrent_destination_is_not_overwritten(self):
        self.env['RACE'] = '1'
        self.assertNotEqual(self.run_script().returncode, 0)
        self.assertEqual(self.destination.read_text(), 'concurrent file')
        self.assertEqual(list(self.destination.parent.glob('.netbird-vendor.*')), [])


class Definition(unittest.TestCase):
    def test_frozen_103_vendor(self):
        vendor = GUEST.parent / 'netbird-router/vendor-data.yaml'
        self.assertEqual(hashlib.sha256(vendor.read_bytes()).hexdigest(),
                         '1c45c56620287cce1825e8af4ed7a03c7f316dd7a10744a5ad102a06a14dc52a')

    def test_embedded_helper_is_exact_and_package_order_pinned(self):
        vendor = (GUEST / 'vendor-data.yaml').read_text()
        helper = vendor.split('path: /usr/local/sbin/netbird-router-enroll\n', 1)[1].split('    content: |\n', 1)[1].split('\nruncmd:', 1)[0]
        helper = '\n'.join(line[6:] for line in helper.rstrip().splitlines()) + '\n'
        self.assertEqual(helper, (GUEST.parent / 'netbird-router/netbird-router-enroll.sh').read_text())
        self.assertIn('package_upgrade: true', vendor)
        for text in ['Pin: version 0.79.0', 'netbird=0.79.0', 'apt-mark hold netbird', '--connect-timeout 10 --max-time 60']:
            self.assertIn(text, vendor)
        commands = vendor.split('runcmd:', 1)[1]
        self.assertLess(commands.index('qemu-guest-agent.service'), commands.index('/usr/local/sbin/netbird-router-bootstrap'))

    def test_procedure_orders_gates_before_permissions_and_enrollment(self):
        text = (GUEST / 'README.md').read_text()
        stages = re.findall(r'^### (\d)\. (.+)$', text, re.MULTILINE)
        self.assertEqual([number for number, _ in stages], list('12345678'))
        labels = dict(stages)
        self.assertIn('Conflict gate', labels['5'])
        self.assertIn('UniFi', labels['6'])
        self.assertIn('Enroll', labels['7'])
        self.assertIn('--ciupgrade 1', text)
        self.assertNotIn('--ciupgrade 0', text)
        stage4 = text.split('### 4.', 1)[1].split('### 5.', 1)[0]
        self.assertIn('HTTPS checks wait for step 6', stage4)


if __name__ == '__main__':
    unittest.main()
