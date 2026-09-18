import base64
import csv
import hashlib
import io
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
SOURCE = HERE / 'flux2-klein-nvfp4-offline/source'
spec = importlib.util.spec_from_file_location('repair', SOURCE / 'repair_cusparselt_metadata.py')
repair = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repair)
provenance = json.loads((SOURCE / 'cusparselt-provenance.json').read_text())
# Captured original WHEEL from the official wheel; RECORD is reconstructed from
# the independently pinned official file identities plus installed pip metadata.
original_wheel = b"Wheel-Version: 1.0\nGenerator: setuptools (80.9.0)\nRoot-Is-Purelib: true\nTag: py3-none-manylinux2014_sbsa\n\n"
record_buffer = io.StringIO(newline="")
record_writer = csv.writer(record_buffer, lineterminator="\r\n")
for name in sorted(set(provenance['files_before']) | {repair.RECORD}):
    if name == repair.RECORD:
        record_writer.writerow([name, '', ''])
    else:
        entry = provenance['files_before'][name]
        encoded = base64.urlsafe_b64encode(bytes.fromhex(entry['sha256'])).decode().rstrip('=')
        record_writer.writerow([name, 'sha256=' + encoded, entry['bytes']])
original_record = record_buffer.getvalue().encode()

class CorrectionTests(unittest.TestCase):
    def test_exact_two_metadata_outputs_and_record_consistency(self):
        wheel, record = repair.corrected_bytes(original_wheel, original_record, provenance)
        self.assertEqual(wheel, original_wheel.replace(b'manylinux2014_sbsa', b'linux_aarch64'))
        expected_files = dict(provenance['files_before'], **{repair.WHEEL: provenance['wheel_after']})
        before_rows = repair.record_rows(original_record, provenance['files_before'])
        after_rows = repair.record_rows(record, expected_files)
        self.assertEqual([old for old, new in zip(before_rows, after_rows) if old != new], [next(row for row in before_rows if row[0] == repair.WHEEL)])
        self.assertEqual(record.count(b'\r\n'), len(before_rows))
        self.assertEqual(hashlib.sha256(record).hexdigest(), 'bfa906941044335c2bdffafca7be80a2e7f1b86d70eec9bc67984a8824ba9215')

    def test_repeated_application_fails_without_writes(self):
        wheel, record = repair.corrected_bytes(original_wheel, original_record, provenance)
        with patch.object(Path, 'write_bytes', side_effect=AssertionError('No write allowed')) as writes:
            with self.assertRaisesRegex(RuntimeError, 'Unexpected size|Unexpected SHA'):
                repair.corrected_bytes(wheel, record, provenance)
            writes.assert_not_called()

    def test_modified_metadata_or_record_rejected(self):
        for wheel, record in [(original_wheel.replace(b'80.9.0', b'80.9.1'), original_record), (original_wheel, original_record.replace(b'273881681', b'273881680'))]:
            with self.subTest(wheel=wheel == original_wheel):
                with self.assertRaises(RuntimeError):
                    repair.corrected_bytes(wheel, record, provenance)

    def test_duplicate_missing_or_outside_record_entries_rejected(self):
        for raw in [original_record + original_record.splitlines(keepends=True)[0], b'\r\n'.join(original_record.split(b'\r\n')[1:]), original_record.replace(b'nvidia/cusparselt/LICENSE.txt', b'../../LICENSE.txt')]:
            with self.assertRaises(RuntimeError):
                repair.record_rows(raw, provenance['files_before'])

    def test_record_hash_size_and_self_entry_rejected(self):
        variants = [original_record.replace(b'17948', b'17949'), original_record.replace(b'sha256=6N', b'sha256=7N'), original_record.replace(b'/RECORD,,', b'/RECORD,sha256=abcd,755')]
        for raw in variants:
            with self.assertRaises(RuntimeError):
                repair.record_rows(raw, provenance['files_before'])

    def test_regular_file_identity_rejects_changed_bytes_and_symlink(self):
        data = b'actual package bytes'
        expected = {'bytes':len(data), 'sha256':hashlib.sha256(data).hexdigest()}
        with tempfile.TemporaryDirectory() as temp:
            file = Path(temp) / 'file'; file.write_bytes(data)
            repair.verify_file(file, expected)
            link = Path(temp) / 'link'; link.symlink_to(file)
            with self.assertRaisesRegex(RuntimeError, 'regular file'):
                repair.verify_file(link, expected)
            file.write_bytes(b'X' + data[1:])
            with self.assertRaisesRegex(RuntimeError, 'SHA-256'):
                repair.verify_file(file, expected)

    def test_actual_platform_and_glibc_floor(self):
        arguments = ['Linux', 'aarch64', 'linux_aarch64', '2.39', {'py3-none-linux_aarch64'}, provenance]
        repair.validate_platform(*arguments)
        for index, bad in [(0,'Darwin'), (1,'x86_64'), (2,'linux_x86_64'), (3,'2.26'), (4,set()), (4,{'py3-none-linux_aarch64','py3-none-manylinux2014_sbsa'})]:
            values = list(arguments); values[index] = bad
            with self.assertRaises(RuntimeError):
                repair.validate_platform(*values)

    def test_precise_single_precheck_and_full_postcheck_required(self):
        result = lambda code, out, err='': subprocess.CompletedProcess([], code, out, err)
        repair.check_pip(result(1, repair.EXPECTED_ERROR+'\n'), before=True)
        for value in [result(0,repair.EXPECTED_ERROR), result(1,repair.EXPECTED_ERROR+'\nanother conflict'), result(1,'other conflict'), result(1,repair.EXPECTED_ERROR,'unexpected warning')]:
            with self.assertRaises(RuntimeError):
                repair.check_pip(value, before=True)
        repair.check_pip(result(0,'No broken requirements found.\n'), before=False)
        with self.assertRaises(RuntimeError):
            repair.check_pip(result(1,repair.EXPECTED_ERROR), before=False)

if __name__ == '__main__':
    unittest.main()
