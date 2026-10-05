import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("collector", Path(__file__).with_name("collect.py"))
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)
NOW = 2_000_000_000


def fixture():
    return {
        "/cluster/status": [{"type": "cluster", "name": "pve-sbx"}, {"type": "node", "name": "pve-sbx-1", "local": 1}],
        "/cluster/resources": [
            {"vmid": 200, "node": "pve-sbx-1", "type": "qemu", "tags": "app;persistent", "template": 0},
            {"vmid": 201, "node": "pve-sbx-1", "type": "qemu", "tags": "disposable"},
            {"vmid": 101, "node": "pve-sbx-2", "type": "qemu", "tags": "persistent"},
            {"vmid": 9000, "node": "pve-sbx-1", "type": "qemu", "tags": "persistent", "template": 1}],
        "/nodes/pve-sbx-1/tasks": [{"type": "vzdump", "id": "", "starttime": NOW-300, "endtime": NOW-200, "status": "OK"}],
        "/nodes/pve-sbx-1/storage/backups-pve-sbx/content": [{"vmid": 200, "ctime": NOW-300}, {"vmid": 200, "ctime": NOW-86400}],
    }


class CollectionTests(unittest.TestCase):
    def observe(self, data):
        return collector.observe(lambda path, **kw: data[path], "pve-sbx", "backups-pve-sbx", NOW)

    def test_inventory_and_newest_archive(self):
        lines = self.observe(fixture())
        self.assertIn(f'pve_backup_archive_timestamp_seconds{{cluster="pve-sbx",id="qemu/200",storage="backups-pve-sbx"}} {NOW-300}', lines)
        self.assertFalse(any('qemu/101' in x or 'qemu/201' in x or 'qemu/9000' in x for x in lines))

    def test_missing_archive_is_zero_only_after_successful_read(self):
        data = fixture(); data['/nodes/pve-sbx-1/storage/backups-pve-sbx/content'] = []
        self.assertTrue(any(x.startswith('pve_backup_archive_') and x.endswith(' 0') for x in self.observe(data)))

    def test_outcomes_are_per_scope_and_ordered_by_completion(self):
        data = fixture(); tasks = data['/nodes/pve-sbx-1/tasks']
        tasks += [dict(tasks[0], id='200', status='job errors', starttime=NOW-500, endtime=NOW-100),
                  dict(tasks[0], id='200', status='OK', starttime=NOW-400, endtime=NOW-150),
                  dict(tasks[0], id='101', status='job errors'), dict(tasks[0], id='999', status='job errors')]
        lines = self.observe(data)
        self.assertIn('pve_backup_task_success{cluster="pve-sbx",scope="200"} 0', lines)
        self.assertIn('pve_backup_task_success{cluster="pve-sbx",scope="batch"} 1', lines)
        self.assertFalse(any('scope="101"' in x or 'scope="999"' in x for x in lines))
        tasks += [dict(tasks[0], id='200', status='OK', endtime=NOW-10)]
        self.assertIn('pve_backup_task_success{cluster="pve-sbx",scope="200"} 1', self.observe(data))

    def test_bad_or_incomplete_data_is_not_success(self):
        for mutate in (
            lambda d: d['/cluster/status'][0].update(name='other'),
            lambda d: d['/nodes/pve-sbx-1/tasks'].__imul__(1000),
            lambda d: d['/nodes/pve-sbx-1/tasks'][0].pop('status'),
            lambda d: d['/nodes/pve-sbx-1/storage/backups-pve-sbx/content'][0].pop('ctime'),
            lambda d: d['/nodes/pve-sbx-1/storage/backups-pve-sbx/content'][0].update(ctime=NOW+86400),
        ):
            data = fixture(); mutate(data)
            with self.assertRaises((ValueError, KeyError)):
                self.observe(data)

    def test_failure_preserves_known_facts_and_recovers(self):
        data = fixture(); data['/nodes/pve-sbx-1/tasks'][0]['status'] = 'job errors'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'pve-backups.prom'
            read = lambda route, **kw: data[route]
            self.assertEqual(collector.run(read, path, 'pve-sbx', 'backups-pve-sbx', NOW), 0)
            facts = [x for x in path.read_text().splitlines() if x.startswith(('pve_backup_task_', 'pve_backup_archive_'))]
            def fail(route, **kw): raise TimeoutError('storage timed out')
            self.assertEqual(collector.run(fail, path, 'pve-sbx', 'backups-pve-sbx', NOW+60), 1)
            result = path.read_text(); self.assertIn('pve_backup_collector_success{cluster="pve-sbx"} 0', result)
            self.assertTrue(all(x in result for x in facts))
            self.assertEqual(collector.run(read, path, 'pve-sbx', 'backups-pve-sbx', NOW+120), 0)
            self.assertIn('pve_backup_collector_success{cluster="pve-sbx"} 1', path.read_text())
            self.assertEqual(path.stat().st_mode & 0o777, 0o644)
            self.assertEqual(list(Path(directory).glob('.pve-backups-*')), [])

    def test_initial_failure_does_not_invent_backup_facts(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'pve-backups.prom'
            def fail(route, **kw): raise ValueError('bad response')
            self.assertEqual(collector.run(fail, path, 'pve-sbx', 'backups-pve-sbx', NOW), 1)
            self.assertFalse('pve_backup_archive_' in path.read_text())
            self.assertFalse('pve_backup_task_' in path.read_text())


if __name__ == '__main__':
    unittest.main()
