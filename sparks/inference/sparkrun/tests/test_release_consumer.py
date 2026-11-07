"""Release consumption must not mix mutable recipes, images or runtime inputs."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('release_consumer', Path(__file__).resolve().parents[2] / 'releases.py')
consumer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(consumer)


class ReleaseConsumerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        self.image = 'ghcr.io/kelchm/spark-inference/glm53-mia@sha256:' + 'a' * 64
        self.release_path = 'recipes/glm53-mia/releases/test.json'
        self.profile_path = 'recipes/glm53-mia/sparkrun/profiles/test.yaml'
        self.files = {
            self.release_path: json.dumps({'release': 'test', 'image': self.image, 'engine': {'commit': 'b' * 40}}).encode(),
            self.profile_path: ('container: ' + json.dumps(self.image) + '\n').encode(),
            'recipes/glm53-mia/sparkrun/mods/native/run.sh': b'#!/bin/sh\nexit 0\n',
        }
        for name, data in self.files.items():
            p = self.source / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        self.lock = {'schema': 'spark-inference-deployment/v1', 'repository': consumer.REPOSITORY,
                     'source_commit': 'c' * 40, 'release': 'test', 'release_path': self.release_path,
                     'profile_path': self.profile_path, 'image': self.image, 'engine_commit': 'b' * 40,
                     'files': {k: consumer.digest(v) for k, v in self.files.items()},
                     'executables': ['recipes/glm53-mia/sparkrun/mods/native/run.sh']}

    def test_materialize_offline_preserves_pins_and_execution_mode(self):
        target = self.root / 'target'
        profile = consumer.materialize(self.lock, target, self.source)
        self.assertEqual(profile, target / self.profile_path)
        self.assertEqual(consumer.check(self.lock, target), profile)
        self.assertTrue((target / self.lock['executables'][0]).stat().st_mode & 0o100)
        self.assertEqual(json.loads((target / consumer.MARKER).read_text()), self.lock)

    def test_changed_input_is_refused_before_output_is_published(self):
        (self.source / self.profile_path).write_text('container: altered\n')
        target = self.root / 'target'
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            consumer.materialize(self.lock, target, self.source)
        self.assertFalse(target.exists())

    def test_profile_cannot_pin_another_image_even_with_matching_file_hash(self):
        data = ('container: ' + self.image.replace('a' * 64, 'd' * 64) + '\n').encode()
        (self.source / self.profile_path).write_bytes(data)
        self.lock['files'][self.profile_path] = consumer.digest(data)
        with self.assertRaisesRegex(ValueError, 'Profile image differs'):
            consumer.materialize(self.lock, self.root / 'target', self.source)

    def test_existing_directory_is_never_overwritten(self):
        target = self.root / 'target'
        target.mkdir()
        (target / 'keep').write_text('retained')
        with self.assertRaisesRegex(ValueError, 'new directory'):
            consumer.materialize(self.lock, target, self.source)
        self.assertEqual((target / 'keep').read_text(), 'retained')

    def test_missing_input_refuses_materialization(self):
        (self.source / self.profile_path).unlink()
        with self.assertRaises(FileNotFoundError):
            consumer.materialize(self.lock, self.root / 'target', self.source)

    def test_linked_source_component_is_refused(self):
        p = self.source / self.profile_path
        actual = self.root / 'actual'
        actual.write_bytes(p.read_bytes())
        p.unlink()
        p.symlink_to(actual)
        with self.assertRaisesRegex(ValueError, 'Linked'):
            consumer.materialize(self.lock, self.root / 'target', self.source)

    def test_unsafe_inventory_paths_and_unpinned_executables_are_refused(self):
        for name in ('../escape', '/absolute', '.', 'a//b', 'a/./b', 'a\\b'):
            with self.subTest(name=name):
                lock = dict(self.lock, files={name: 'a' * 64})
                with self.assertRaises(ValueError):
                    consumer.validate(lock)
        with self.assertRaisesRegex(ValueError, 'Unpinned executable'):
            consumer.validate(dict(self.lock, executables=['not-pinned']))

    def test_mutable_ref_or_image_is_refused(self):
        with self.assertRaises(ValueError):
            consumer.validate(dict(self.lock, source_commit='main'))
        with self.assertRaises(ValueError):
            consumer.validate(dict(self.lock, image='ghcr.io/kelchm/spark-inference/glm53-mia:latest'))

    def test_check_detects_materialized_input_change(self):
        target = self.root / 'target'
        consumer.materialize(self.lock, target, self.source)
        (target / self.profile_path).write_text('changed')
        with self.assertRaisesRegex(ValueError, 'differs'):
            consumer.check(self.lock, target)

    def test_check_detects_changed_executable_mode(self):
        target = self.root / 'target'
        consumer.materialize(self.lock, target, self.source)
        (target / self.lock['executables'][0]).chmod(0o644)
        with self.assertRaisesRegex(ValueError, 'executable mode differs'):
            consumer.check(self.lock, target)


if __name__ == '__main__':
    unittest.main()
