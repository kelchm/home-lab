#!/usr/bin/env python3
"""Exercise Kanidm version boundaries without fetching an upstream release."""

from pathlib import Path
import runpy
import unittest


checker = runpy.run_path(str(Path(__file__).with_name("validate-kanidm-compatibility.py")))
check_compatibility = checker["check_compatibility"]
kanidm_client_version = checker["kanidm_client_version"]

CLIENT = '[[package]]\nname = "kanidm_client"\nversion = "1.12.1"\n'


class CompatibilityTest(unittest.TestCase):
    def test_reads_client_version_by_package_name(self):
        server = '[[package]]\nname = "kanidm_proto"\nversion = "1.13.0"\n'
        self.assertEqual(kanidm_client_version(server + CLIENT), "1.12.1")

    def test_rejects_missing_or_ambiguous_client(self):
        for lockfile in ("version = 4\n", CLIENT + CLIENT):
            with self.subTest(lockfile=lockfile):
                with self.assertRaisesRegex(ValueError, "exactly one kanidm_client"):
                    kanidm_client_version(lockfile)

    def test_accepts_older_minor_and_any_patch_in_the_sdk_minor(self):
        for tag in ("1.11.2", "1.12.0", "1.12.99", "v1.12.0-rc.1"):
            with self.subTest(tag=tag):
                check_compatibility(f"kanidm/server:{tag}", "1.12.1")
        check_compatibility(f"docker.io/kanidm/server:1.12.3@sha256:{'a' * 64}", "1.12.1")

    def test_rejects_newer_minor_and_different_major(self):
        for tag in ("1.13.0", "2.0.0", "0.12.0"):
            with self.subTest(tag=tag):
                with self.assertRaisesRegex(ValueError, f"Kanidm {tag} is incompatible"):
                    check_compatibility(f"kanidm/server:{tag}", "1.12.1")

    def test_rejects_unversioned_image_and_unparseable_sdk(self):
        for image in ("kanidm/server", "kanidm/server:latest", f"kanidm/server@sha256:{'a' * 64}"):
            with self.subTest(image=image):
                with self.assertRaisesRegex(ValueError, "versioned Kanidm server image"):
                    check_compatibility(image, "1.12.1")
        with self.assertRaisesRegex(ValueError, "unexpected kanidm_client version"):
            check_compatibility("kanidm/server:1.12.0", "unknown")


if __name__ == "__main__":
    unittest.main()
