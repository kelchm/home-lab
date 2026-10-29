#!/usr/bin/python3
"""Attended, one-time preparation from a reviewed checkout. Never recreates containers."""

import argparse
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent
GUEST = HERE.parent


def prepare(project, state, expected, call, revision="unknown"):
    # Refuse ambiguous adoption rather than replacing existing operator settings.
    if any((project / name).exists() for name in (".env", "compose.caddy-image.yaml", "compose.yaml.pre-caddy-delivery")) or state.exists():
        raise RuntimeError("existing delivery state: reconcile manually before installation")
    running = json.loads(call(["docker", "inspect", "--format",
                              '[{{json .Image}},{{json (index .Config.Labels "com.docker.compose.project")}}]',
                              "hermes-caddy"]))
    if running != [expected, "hermes"]:
        raise RuntimeError("running Caddy image/project differs from attended baseline")
    legacy = "localhost/hermes-caddy:legacy-" + expected.removeprefix("sha256:")[:12]
    # Inspect by ID before tagging; a tag can never supply different rollback bytes.
    call(["docker", "image", "inspect", "--format", "{{.Id}}", expected])
    existing = call(["docker", "image", "ls", "--quiet", "--no-trunc", legacy]).strip()
    if existing and existing != expected:
        raise RuntimeError("legacy tag conflict: stop and reconcile")
    with tempfile.TemporaryDirectory(dir=project, prefix=".caddy-prepare-") as directory:
        staging = Path(directory)
        base = staging / "compose.yaml"
        base.write_bytes((GUEST / "compose.yaml").read_bytes())
        image = staging / "image.yaml"
        image.write_text(f"services:\n  caddy:\n    image: {legacy}\n")
        prefix = ["docker", "compose", "-p", "hermes", "--project-directory", str(project), "--env-file", "/dev/null"]
        old = json.loads(call([*prefix, "-f", str(project / "compose.yaml"), "config", "--no-env-resolution", "--format", "json"]))
        new = json.loads(call([*prefix, "-f", str(base), "-f", str(image), "config", "--no-env-resolution", "--format", "json"]))
        # The sole accepted migration: remove build, select image, make effective UID explicit.
        before, after = old["services"]["caddy"], new["services"]["caddy"]
        before.pop("build", None)
        before["image"] = after["image"]
        before["user"] = "1000:1000"
        if old != new:
            raise RuntimeError("effective Compose differs outside the reviewed Caddy migration")
    os.umask(0o077)
    state.mkdir(mode=0o700)
    (state / "paused").touch()
    # Save the baseline before staging; any interrupted install remains paused.
    shutil.copy2(project / "compose.yaml", project / "compose.yaml.pre-caddy-delivery")
    call(["docker", "image", "tag", expected, legacy])
    helper = runpy.run_path(str(HERE / "hermes-caddy-update"))
    helper["atomic"](state / "installation.json", json.dumps({"source_revision": revision,
                      "controller_sha256": helper["CONTROLLER_SHA256"]}) + "\n")
    helper["atomic"](state / "current.json", json.dumps({"ref": legacy, "id": expected,
                       "oci_revision": "unknown", "run_url": "unknown", "main_sha": "unknown"}) + "\n")
    helper["atomic"](project / "compose.yaml", (GUEST / "compose.yaml").read_text())
    helper["atomic"](project / "compose.caddy-image.yaml", f"services:\n  caddy:\n    image: {legacy}\n")
    helper["atomic"](project / ".env", "COMPOSE_FILE=compose.yaml:compose.caddy-image.yaml\nCOMPOSE_PROJECT_NAME=hermes\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--legacy-image-id", required=True)
    args = parser.parse_args()
    if os.geteuid() != 0 or not re.fullmatch(r"sha256:[0-9a-f]{64}", args.legacy_image_id):
        parser.error("root and the attended full running legacy config ID are required")
    for path in (Path("/opt/hermes"), Path("/opt/hermes/compose.yaml"), Path("/opt/hermes/Caddyfile"), Path("/opt/hermes/managed")):
        if path.is_symlink() or path.stat().st_uid != 0 or path.stat().st_mode & 0o022:
            parser.error("existing project/config must be root-owned and not group/world writable")
    # Commands are fixed by this reviewed installer, never read from Git remotely.
    def call(argv):
        result = subprocess.run(argv, capture_output=True, text=True, timeout=30, check=False,
                                env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "HOME": "/root"})
        if result.returncode:
            raise RuntimeError("preparation command failed; output suppressed")
        return result.stdout
    revision = call(["git", "-C", str(GUEST), "rev-parse", "HEAD"]).strip()
    call(["git", "-C", str(GUEST), "diff", "--quiet", "HEAD", "--", "."])
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise RuntimeError("reviewed checkout revision is required")
    prepare(Path("/opt/hermes"), Path("/var/lib/hermes-caddy"), args.legacy_image_id, call, revision)
    for source, destination, mode in (
        (HERE / "hermes-caddy-update", Path("/usr/local/sbin/hermes-caddy-update"), 0o755),
        *((HERE / ("hermes-caddy-update." + suffix), Path("/etc/systemd/system/hermes-caddy-update." + suffix), 0o644)
          for suffix in ("service", "timer")),
    ):
        shutil.copyfile(source, destination)
        destination.chmod(mode)
    Path("/etc/hermes-caddy").mkdir(mode=0o700, exist_ok=True)
    call(["systemctl", "daemon-reload"])
    print("Prepared paused; no container recreated and no timer enabled. Follow commissioning runbook.")


if __name__ == "__main__":
    main()
