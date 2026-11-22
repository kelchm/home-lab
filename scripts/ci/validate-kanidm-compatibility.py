#!/usr/bin/env python3
"""Check the proposed Kanidm image against the pinned Kaniop release."""

from pathlib import Path
import re
import subprocess
import sys
import tomllib


IDENTITY = Path(__file__).resolve().parents[2] / "kubernetes/apps/identity"
RELEASE = r"([0-9]+)\.([0-9]+)\.[0-9]+(?:-[0-9A-Za-z.-]+)?"


def read_pin(path, expression):
    result = subprocess.run(
        ["yq", "--exit-status", "--unwrapScalar", expression, str(IDENTITY / path)],
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    )
    return result.stdout.strip()


def kanidm_client_version(lockfile):
    packages = tomllib.loads(lockfile).get("package", [])
    versions = [p.get("version") for p in packages if p.get("name") == "kanidm_client"]
    if len(versions) != 1 or not isinstance(versions[0], str):
        raise ValueError("expected exactly one kanidm_client version in Cargo.lock")
    return versions[0]


def check_compatibility(image, sdk_version):
    image_name, _, tag = image.split("@", 1)[0].rsplit("/", 1)[-1].partition(":")
    server = re.fullmatch(f"v?{RELEASE}", tag)
    if not image_name or server is None:
        raise ValueError(f"expected a versioned Kanidm server image, got {image!r}")
    sdk = re.fullmatch(RELEASE, sdk_version)
    if sdk is None:
        raise ValueError(f"unexpected kanidm_client version {sdk_version!r} in Cargo.lock")

    # Kaniop's version.rs ignores patch versions and accepts older server minors.
    server_major, server_minor = map(int, server.groups())
    sdk_major, sdk_minor = map(int, sdk.groups())
    if server_major != sdk_major or server_minor > sdk_minor:
        raise ValueError(
            f"Kanidm {tag} is incompatible with kanidm_client {sdk_version}. "
            f"Use a {sdk_major}.x server at minor {sdk_minor} or earlier, "
            "or update Kaniop to a compatible release first."
        )


def main():
    try:
        kaniop = read_pin("kaniop/app/ocirepository.yaml", ".spec.ref.tag")
        if re.fullmatch(RELEASE, kaniop) is None:
            raise ValueError(f"expected a Kaniop release tag, got {kaniop!r}")
        image = read_pin("kanidm/app/kanidm.yaml", ".spec.image")
        print(f"Checking {image} with Kaniop {kaniop}", flush=True)
        lockfile = subprocess.run(
            [
                "curl", "-fsSL", "--retry", "2", "--retry-max-time", "20",
                "--connect-timeout", "5", "--max-time", "10",
                f"https://raw.githubusercontent.com/pando85/kaniop/v{kaniop}/Cargo.lock",
            ],
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        ).stdout
        sdk_version = kanidm_client_version(lockfile)
        check_compatibility(image, sdk_version)
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        print(f"Kanidm compatibility check failed: {exc}", file=sys.stderr)
        return 1
    print(f"Compatible with the bundled kanidm_client {sdk_version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
