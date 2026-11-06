import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

spec = importlib.util.spec_from_file_location('preview', Path(__file__).resolve().parents[1] / 'preview.py')
preview = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preview)


class NativeCopies(unittest.TestCase):
    def test_v1_replaces_pinned_sources_and_preserves_link_source(self):
        original = {'uid': 'native', 'title': 'Native', 'panels': [{'datasource': {'type': 'prometheus', 'uid': 'production'}, 'targets': [{'datasource': 'victoriametrics'}]}], 'templating': {'list': [{'name': 'datasource', 'type': 'datasource', 'current': {'value': 'production'}}]}, 'links': [{'url': '/d/another?var-node=node-a', 'includeVars': False}]}
        d = preview.native_dashboard(original, {'from': '2026-11-01', 'to': '2026-11-02'})
        self.assertEqual(d['panels'][0]['datasource']['uid'], '${datasource}')
        self.assertEqual(d['panels'][0]['targets'][0]['datasource'], '${datasource}')
        self.assertEqual(d['templating']['list'][0]['current']['value'], 'comparison')
        self.assertIn('var-datasource=${datasource}', d['links'][0]['url'])
        self.assertTrue(d['links'][0]['includeVars'])
        self.assertEqual(original['panels'][0]['datasource']['uid'], 'production')

    def test_v2_query_data_sources_and_range_use_the_explicit_source(self):
        d = preview.native_dashboard({'kind': 'Dashboard', 'metadata': {'name': 'home'}, 'spec': {'title': 'Home', 'timeSettings': {}, 'variables': [], 'elements': {'x': {'group': 'prometheus', 'datasource': {'name': 'victoriametrics'}}}}}, {'from': 'old-start', 'to': 'old-end'})
        self.assertEqual(d['spec']['elements']['x']['datasource']['name'], '${datasource}')
        self.assertEqual(d['spec']['timeSettings']['to'], 'old-end')
        self.assertEqual(d['spec']['variables'][0]['spec']['current']['value'], 'comparison')

    def test_immutable_publication_reuses_content_and_rejects_corruption(self):
        with tempfile.TemporaryDirectory() as temp:
            target, sha = preview.immutable(Path(temp), {'module.js': b'module'})
            self.assertEqual(preview.immutable(Path(temp), {'module.js': b'module'}), (target, sha))
            (target / 'module.js').write_text('changed')
            with self.assertRaisesRegex(RuntimeError, 'Immutable artifact changed'):
                preview.immutable(Path(temp), {'module.js': b'module'})

    def test_rebuilding_test_lanes_preserves_the_viewer_registry_and_namespaces_state(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            args = SimpleNamespace(output_dir=root / 'out', state_dir=root / 'state', log_dir=root / 'logs', project='preview-one', live_url='http://live.invalid', ports=[3431, 3432, 3433, 3434, 3435, 3436], lanes=['viewer'], fixture_image='node:24.22.1-alpine', grafana_image='grafana/grafana:13.3.0-distroless')
            source = root / 'source'; source.mkdir(); (source / 'placeholder').write_text('input')
            viewer = preview.compose(args, {}, source, source)['viewer']
            args.lanes = ['test-a', 'test-b']
            preview.compose(args, {}, source, source)
            registry = json.loads((args.output_dir / 'preview-one-lanes.json').read_text())
            self.assertEqual(registry['viewer'], viewer)
            self.assertEqual(set(registry), {'viewer', 'test-a', 'test-b'})
            self.assertEqual(Path(registry['test-a']['state']), root / 'state/preview-one/test-a')


if __name__ == '__main__':
    unittest.main()
