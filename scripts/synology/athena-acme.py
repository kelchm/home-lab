#!/usr/bin/env python3
"""Root-only, native DSM certificate renewal for athena.home.kelch.io.

Python 3.8 / standard library only. Installation and initial DSM UI binding are
separate operator actions. This program never logs in, edits nginx, or reloads a
service. Only the native certificate import API may activate a certificate.
"""

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import socket
import ssl
import stat
import subprocess
import sys
import tempfile
import time

HOST = "athena.home.kelch.io"
ROOT = Path("/volume1/@athena-acme")
DESCRIPTION = "athena-dsm-acme"
ORIGINAL_ID = "ALK0dn"
SYNOWEBAPI = "/usr/syno/bin/synowebapi"
OPENSSL = "/usr/bin/openssl"
# Annotated v3.1.6 tag c6e7807f8e2573bf8b02b71da186b4022afafa56,
# dereferenced source commit 807da6498377ee5e0cf43a78091f46f12dc59a89.
SOURCE_HASHES = {
    "acme.sh": "c7d68b021cfd6380ea83a82962abde5b484779fee0b97d38681dfa1396bbc8d7",
    "dnsapi/dns_cf.sh": "9628ee8238cb3f9cfa1b1a985c0e9593436a3e4f8a9d65a6f775b981be9e76c8",
}
FILES = ("cert.pem", "key.pem", "chain.pem", "fullchain.pem")
MIN_VALIDITY = 21 * 86400
FRESHNESS = 26 * 3600
MAX_OUTPUT = 1024 * 1024


class Failure(Exception):
    """A deliberately sanitized operator-facing failure."""


def require(condition, message):
    if not condition:
        raise Failure(message)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def parse_api(output):
    """Accept the known DSM text preamble, but exactly one JSON response."""
    try:
        text = output.decode("utf-8")
        # DSM writes param={} on its diagnostic preamble line. The response
        # JSON starts on its own line; do not mistake that parameter for it.
        start = re.search(r"(?m)^[ \t]*\{", text)
        if start is None:
            raise ValueError("missing JSON response")
        result = json.loads(text[start.start():])
    except (ValueError, UnicodeError):
        raise Failure("DSM API returned an invalid JSON response")
    require(isinstance(result, dict) and result.get("success") is True,
            "DSM API did not report success")
    return result


def certificate_map(response):
    try:
        certificates = response["data"]["certificates"]
        require(isinstance(certificates, list) and certificates, "DSM certificate list is empty")
        result = {}
        seen_services = set()
        for cert in certificates:
            cert_id = cert["id"]
            require(isinstance(cert_id, str) and re.fullmatch(r"[A-Za-z0-9_-]+", cert_id),
                    "DSM certificate ID is invalid")
            require(cert_id not in result, "DSM returned duplicate certificate IDs")
            require(isinstance(cert["desc"], str) and type(cert["is_default"]) is bool,
                    "DSM certificate metadata is invalid")
            require(cert.get("is_broken") is False, "DSM contains a broken certificate")
            services = cert["services"]
            require(isinstance(services, list), "DSM service assignments are invalid")
            for service in services:
                require(isinstance(service, dict), "DSM service assignment is invalid")
                identity = tuple(service[key] for key in ("owner", "subscriber", "service"))
                require(all(isinstance(item, str) and item for item in identity),
                        "DSM service identity is invalid")
                require(identity not in seen_services, "DSM returned duplicate service assignments")
                seen_services.add(identity)
            result[cert_id] = {"desc": cert["desc"], "is_default": cert["is_default"],
                               "services": sorted(services, key=canonical)}
        require(sum(item["is_default"] for item in result.values()) == 1,
                "DSM must have exactly one default certificate")
        return result
    except (KeyError, TypeError):
        raise Failure("DSM certificate list schema is invalid")


def named_certificate(mapping):
    matches = [key for key, value in mapping.items() if value["desc"] == DESCRIPTION]
    require(len(matches) == 1, "Expected exactly one athena-dsm-acme certificate")
    require(matches[0] != ORIGINAL_ID, "Refusing to replace the original DSM certificate")
    return matches[0]


def accepted_transition(baseline, current, cert_id):
    """The one permitted manual UI change: move only system/default."""
    require(cert_id != ORIGINAL_ID and ORIGINAL_ID in baseline,
            "Initial certificate identity is invalid")
    expected = json.loads(canonical(baseline))
    require(cert_id not in expected, "Initial certificate already existed in baseline")
    require(expected[ORIGINAL_ID]["is_default"], "Original DSM certificate was not default")
    original = expected[ORIGINAL_ID]
    moved = [service for service in original["services"]
             if tuple(service[key] for key in ("owner", "subscriber", "service"))
             == ("root", "system", "default")]
    require(len(moved) == 1, "Baseline system/default assignment is missing")
    original["services"] = [service for service in original["services"] if service not in moved]
    original["is_default"] = False
    expected[cert_id] = {"desc": DESCRIPTION, "is_default": True, "services": moved}
    require(current == expected, "DSM bindings changed beyond system/default; acceptance refused")


class Runner:
    def __init__(self, root=ROOT):
        self.root = Path(root)

    def path(self, name):
        return self.root / name

    def guard(self, path, directory=False):
        try:
            info = path.lstat()
        except OSError:
            raise Failure("Required protected runtime path is missing")
        expected_mode = 0o700 if directory else 0o600
        require(info.st_uid == 0 and stat.S_IMODE(info.st_mode) == expected_mode,
                "Runtime paths must be owned by root with directory 0700 / file 0600 modes")
        require(stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode),
                "Runtime paths must be regular files/directories; symlinks are forbidden")
        return info

    def read(self, path):
        before = self.guard(path)
        descriptor = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as stream:
            after = os.fstat(stream.fileno())
            require((before.st_dev, before.st_ino) == (after.st_dev, after.st_ino),
                    "Runtime file changed during inspection")
            result = stream.read(MAX_OUTPUT + 1)
        require(len(result) <= MAX_OUTPUT, "Runtime file exceeds the size limit")
        return result

    def load(self, name):
        try:
            value = json.loads(self.read(self.path(name)).decode("utf-8"))
            require(isinstance(value, dict), "Runtime state must be a JSON object")
            return value
        except (ValueError, UnicodeError):
            raise Failure("Runtime JSON state is invalid")

    def write(self, path, data):
        self.guard(path.parent, directory=True)
        if path.exists() or path.is_symlink():
            self.guard(path)
        descriptor, temporary = tempfile.mkstemp(prefix=".write-", dir=str(path.parent))
        try:
            with os.fdopen(descriptor, "wb") as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, str(path))
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def save(self, name, value):
        self.write(self.path(name), (canonical(value) + "\n").encode("utf-8"))

    def audit(self):
        require(os.geteuid() == 0, "Run as root on Athena")
        # A private child below a world-writable shared folder can be renamed
        # and replaced. The entire runtime path must therefore be protected.
        for ancestor in self.root.parents:
            info = ancestor.lstat()
            require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0
                    and not (stat.S_IMODE(info.st_mode) & 0o022),
                    "Runtime ancestors must be root-owned directories without group/world write access")
        self.guard(self.root, directory=True)
        for base, directories, files in os.walk(str(self.root), followlinks=False):
            for name in directories:
                self.guard(Path(base) / name, directory=True)
            for name in files:
                self.guard(Path(base) / name)
        client = self.path("client")
        self.guard(client, directory=True)
        actual = {str(path.relative_to(client)) for path in client.rglob("*") if path.is_file()}
        require(actual == set(SOURCE_HASHES), "Only the pinned ACME core and Cloudflare DNS plugin may be installed")
        for name, digest in SOURCE_HASHES.items():
            require(hashlib.sha256(self.read(client / name)).hexdigest() == digest,
                    "Pinned ACME source hash mismatch")
        self.check_config()

    def check_config(self):
        # acme.sh sources its private shell configuration. Reject saved execution
        # hooks and account-wide automatic upgrades before invoking it at all.
        for folder in (self.path("state"), self.path("certs")):
            if not folder.exists():
                continue
            for path in folder.rglob("*.conf"):
                text = self.read(path).decode("utf-8")
                for line in text.splitlines():
                    match = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z_0-9]*)=(.*)$", line)
                    if not match:
                        continue
                    key, value = match.groups()
                    empty = value.strip() in ("", "''", '""')
                    if "Hook" in key or key in ("Le_ReloadCmd", "NOTIFY_HOOK"):
                        require(empty, "Saved ACME execution hooks are forbidden")
                    if key == "AUTO_UPGRADE":
                        require(value.strip() in ("0", "'0'", '"0"'), "ACME automatic upgrades are forbidden")
                    if key in ("Le_Domain", "Le_Alt", "Le_Webroot", "Le_Keylength"):
                        expected = {"Le_Domain": HOST, "Le_Alt": "no", "Le_Webroot": "dns_cf",
                                    "Le_Keylength": "ec-256"}[key]
                        require(value.strip() in (expected, "'" + expected + "'", '"' + expected + '"'),
                                "Saved ACME domain configuration is outside the allowed scope")

    @contextlib.contextmanager
    def lock(self):
        path = self.path("lock")
        if path.exists() or path.is_symlink():
            self.guard(path)
        descriptor = os.open(str(path), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(descriptor)
            require(info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o600
                    and stat.S_ISREG(info.st_mode), "Runtime lock is not protected")
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Failure("Another Athena ACME operation is running")
            yield
        finally:
            os.close(descriptor)

    def diagnostic(self, output):
        # Only private diagnostics retain child output, which may contain a
        # credential. Scheduler errors never include this text.
        self.write(self.path("last-error.log"), output[:65536])

    def command(self, argv, env=None, timeout=120):
        # Outputs may contain Cloudflare credentials. Keep them in an unlinked,
        # root-only temporary file; never include command/output in exceptions.
        environment = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": "/root", "LC_ALL": "C"}
        if env:
            environment.update(env)
        with tempfile.TemporaryFile(dir=str(self.root)) as output:
            process = subprocess.Popen(argv, stdout=output, stderr=subprocess.STDOUT,
                                       env=environment, start_new_session=True)
            deadline = time.monotonic() + timeout
            while process.poll() is None:
                if time.monotonic() >= deadline or os.fstat(output.fileno()).st_size > MAX_OUTPUT:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                    output.seek(0)
                    self.diagnostic(output.read(65536))
                    raise Failure("External command timed out or exceeded its output limit")
                try:
                    process.wait(timeout=0.1)
                except subprocess.TimeoutExpired:
                    pass
            output.seek(0)
            data = output.read(MAX_OUTPUT + 1)
            if len(data) > MAX_OUTPUT:
                self.diagnostic(data)
                raise Failure("External command exceeded its output limit")
            return process.returncode, data

    def checked(self, argv):
        code, output = self.command(argv)
        if code != 0:
            self.diagnostic(output)
            raise Failure("Certificate validation command failed")
        return output

    def api(self, method, parameters=None):
        api = "SYNO.Core.Certificate.CRT" if method == "list" else "SYNO.Core.Certificate"
        argv = [SYNOWEBAPI, "--exec", "api=" + api, "method=" + method, "version=1"]
        argv.extend(key + "=" + json.dumps(value) for key, value in (parameters or {}).items())
        code, output = self.command(argv)
        if code != 0:
            self.diagnostic(output)
            raise Failure("Native DSM certificate API command failed")
        try:
            return parse_api(output)
        except Failure:
            self.diagnostic(output)
            raise

    def snapshot(self):
        return certificate_map(self.api("list"))

    def current_accepted(self):
        accepted = self.load("accepted.json")
        current = self.snapshot()
        cert_id = named_certificate(current)
        require(cert_id == accepted.get("cert_id") and current == accepted.get("mapping"),
                "DSM certificate identity, default, or service assignments drifted")
        return accepted

    def token(self):
        token = self.read(self.path("cloudflare-token")).decode("ascii").strip()
        require(bool(re.fullmatch(r"[A-Za-z0-9_-]{20,256}", token)), "Cloudflare credential format is invalid")
        return token

    def acme(self, initial=False):
        token = self.token()
        common = ["/bin/sh", str(self.path("client/acme.sh")),
                  "--home", str(self.path("client")), "--config-home", str(self.path("state")),
                  "--cert-home", str(self.path("certs")), "--server", "letsencrypt"]
        domain_conf = self.path("certs") / (HOST + "_ecc") / (HOST + ".conf")
        if initial and not domain_conf.exists():
            action = ["--issue", "--dns", "dns_cf", "-d", HOST, "--keylength", "ec-256"]
        else:
            action = ["--renew", "-d", HOST, "--ecc"]
        code, output = self.command(common + action, env={"CF_Token": token}, timeout=900)
        if not (code == 0 or (code == 2 and "--renew" in action)):
            self.diagnostic(output)
            raise Failure("ACME issue/renew failed")
        self.audit()
        install = ["--install-cert", "-d", HOST, "--ecc", "--cert-file", str(self.path("issued/cert.pem")),
                   "--key-file", str(self.path("issued/key.pem")), "--ca-file", str(self.path("issued/chain.pem")),
                   "--fullchain-file", str(self.path("issued/fullchain.pem"))]
        code, output = self.command(common + install, env={"CF_Token": token}, timeout=120)
        if code != 0:
            self.diagnostic(output)
            raise Failure("ACME stable certificate installation failed")
        self.audit()
        return self.bundle("issued")

    def bundle(self, directory):
        return {name: self.path(directory) / name for name in FILES}

    def validate(self, bundle):
        for path in bundle.values():
            self.read(path)
        cert, key, chain = (str(bundle[name]) for name in FILES[:3])
        public_cert = self.checked([OPENSSL, "x509", "-in", cert, "-pubkey", "-noout"])
        public_key = self.checked([OPENSSL, "pkey", "-in", key, "-pubout"])
        require(public_cert == public_key, "Issued certificate does not match its private key")
        san = self.checked([OPENSSL, "x509", "-in", cert, "-noout", "-ext", "subjectAltName"])
        names = [item.strip() for item in b"\n".join(san.splitlines()[1:]).decode("ascii").split(",")]
        require(names == ["DNS:" + HOST], "Certificate must have exactly the Athena DNS SAN")
        self.checked([OPENSSL, "x509", "-in", cert, "-noout", "-checkend", str(MIN_VALIDITY)])
        self.checked([OPENSSL, "verify", "-purpose", "sslserver", "-verify_hostname", HOST,
                      "-untrusted", chain, cert])
        # Import consumes leaf + intermediates; verify stable fullchain agrees too.
        leaf = ssl.PEM_cert_to_DER_cert(self.read(bundle["cert.pem"]).decode("ascii"))
        fullchain = self.read(bundle["fullchain.pem"])
        require(fullchain.strip() == (self.read(bundle["cert.pem"]).strip() + b"\n" +
                                      self.read(bundle["chain.pem"]).strip()),
                "Stable fullchain does not match leaf and intermediate bundle")
        return hashlib.sha256(leaf).hexdigest()

    def live(self, expected=None, minimum=MIN_VALIDITY):
        fingerprints = []
        for port in (443, 5001):
            context = ssl.create_default_context()
            with socket.create_connection(("127.0.0.1", port), timeout=15) as connection:
                with context.wrap_socket(connection, server_hostname=HOST) as tls:
                    cert = tls.getpeercert()
                    require(cert.get("subjectAltName") == (("DNS", HOST),),
                            "Live certificate must have exactly the Athena DNS SAN")
                    require(ssl.cert_time_to_seconds(cert["notAfter"]) - time.time() > minimum,
                            "Live certificate expires within the required validity window")
                    fingerprints.append(hashlib.sha256(tls.getpeercert(binary_form=True)).hexdigest())
        require(fingerprints[0] == fingerprints[1], "Ports 443 and 5001 serve different certificates")
        require(expected is None or fingerprints[0] == expected, "DSM is not serving the expected certificate")
        return fingerprints[0]

    def archive(self, bundle, fingerprint):
        directory = self.path("bundles") / fingerprint
        if not directory.exists():
            # A crash must not leave a partial immutable archive blocking every
            # retry of this leaf. Publish the complete directory in one rename.
            with tempfile.TemporaryDirectory(prefix="bundle-", dir=str(self.path("bundles"))) as stage:
                for name, source in bundle.items():
                    self.write(Path(stage) / name, self.read(source))
                os.rename(stage, str(directory))
        archived = self.bundle("bundles/" + fingerprint)
        require(self.validate(archived) == fingerprint, "Archived certificate bundle is inconsistent")
        return {"sha256": fingerprint}

    def saved_bundle(self, pointer, validate=True):
        fingerprint = pointer.get("sha256", "")
        require(isinstance(fingerprint, str) and re.fullmatch(r"[a-f0-9]{64}", fingerprint),
                "Saved certificate pointer is invalid")
        bundle = self.bundle("bundles/" + fingerprint)
        if validate:
            require(self.validate(bundle) == fingerprint, "Saved certificate bundle does not match its pointer")
        else:
            # A successful renewal must still recover when the prior good leaf
            # has aged out. Preserve its protected bytes without requiring it
            # to remain a currently deployable certificate.
            for path in bundle.values():
                self.read(path)
            leaf = ssl.PEM_cert_to_DER_cert(self.read(bundle["cert.pem"]).decode("ascii"))
            require(hashlib.sha256(leaf).hexdigest() == fingerprint, "Saved certificate fingerprint changed")
        return bundle

    def import_bundle(self, bundle, cert_id=None, is_default=False):
        require(cert_id != ORIGINAL_ID, "Refusing to replace the original DSM certificate")
        # DSM may move the supplied paths; originals and rollback remain intact.
        with tempfile.TemporaryDirectory(prefix="import-", dir=str(self.root)) as stage:
            parameters = {"desc": DESCRIPTION, "as_default": "true" if is_default else "false"}
            for field, filename in (("key_tmp", "key.pem"), ("cert_tmp", "cert.pem"),
                                    ("inter_cert_tmp", "chain.pem")):
                target = Path(stage) / filename
                self.write(target, self.read(bundle[filename]))
                parameters[field] = str(target)
            if cert_id is not None:
                parameters["id"] = cert_id
            return self.api("import", parameters)

    def verified(self, bundle, fingerprint):
        self.live(fingerprint)
        self.current_accepted()
        pointer = self.archive(bundle, fingerprint)
        self.save("good.json", pointer)
        self.save("last-verified-success.json", {"at": int(time.time()), "sha256": fingerprint})

    def inspect(self):
        current = self.snapshot()
        print(json.dumps({"hostname": HOST, "certificates": current}, indent=2, sort_keys=True))

    def issue(self):
        require(not self.path("accepted.json").exists(), "Use run after initial acceptance")
        self.validate(self.acme(initial=True))

    def import_new(self):
        require(not self.path("accepted.json").exists(), "Initial import is already accepted")
        bundle = self.bundle("issued")
        fingerprint = self.validate(bundle)
        current = self.snapshot()
        matches = [key for key, cert in current.items() if cert["desc"] == DESCRIPTION]
        if self.path("baseline.json").exists():
            baseline = self.load("baseline.json")["mapping"]
        else:
            require(not matches and ORIGINAL_ID in current and current[ORIGINAL_ID]["is_default"],
                    "Initial import requires the untouched original DSM default")
            baseline = current
            self.save("baseline.json", {"mapping": baseline})
        if not matches:
            require(current == baseline, "DSM assignments changed since the initial snapshot")
            response = self.import_bundle(bundle)
            current = self.snapshot()
            cert_id = named_certificate(current)
            returned_id = response.get("data", {}).get("id")
            require(returned_id == cert_id, "DSM import response does not identify the new certificate")
        else:
            # Recover a successful import whose response/postcheck was interrupted.
            cert_id = named_certificate(current)
        expected = dict(baseline)
        expected[cert_id] = {"desc": DESCRIPTION, "is_default": False, "services": []}
        require(current == expected, "Initial import changed existing defaults or assignments")
        self.save("candidate.json", {"cert_id": cert_id, "sha256": fingerprint})
        print("Initial certificate imported. Bind only DSM system/default in the UI, then run accept.")

    def accept(self):
        require(not self.path("accepted.json").exists(), "Initial acceptance already exists")
        candidate = self.load("candidate.json")
        current = self.snapshot()
        cert_id = named_certificate(current)
        require(cert_id == candidate.get("cert_id"), "Initial certificate identity changed")
        accepted_transition(self.load("baseline.json")["mapping"], current, cert_id)
        bundle = self.bundle("issued")
        fingerprint = self.validate(bundle)
        require(fingerprint == candidate.get("sha256"), "Issued certificate changed after initial import")
        self.live(fingerprint)
        require(self.snapshot() == current, "DSM assignments changed during initial acceptance")
        # Prepare recovery state before committing the accepted identity. A
        # later failed live check leaves run able to retry safely.
        self.save("good.json", self.archive(bundle, fingerprint))
        self.save("accepted.json", {"cert_id": cert_id, "mapping": current})
        self.verified(bundle, fingerprint)

    def run(self):
        self.current_accepted()
        bundle = self.acme()
        fingerprint = self.validate(bundle)
        good = self.load("good.json")
        self.saved_bundle(good, validate=False)
        try:
            live_fingerprint = self.live(minimum=0)
        except (Failure, OSError, ssl.SSLError, ValueError):
            live_fingerprint = None
        if live_fingerprint != fingerprint:
            # Before every import, preserve the last fully verified bundle. On a
            # failed import retry this still points to the same known-good leaf.
            if good.get("sha256") != fingerprint:
                self.save("previous.json", good)
            self.replace(bundle, fingerprint)
        else:
            self.verified(bundle, fingerprint)

    def replace(self, bundle, fingerprint):
        accepted = self.current_accepted()
        self.import_bundle(bundle, accepted["cert_id"], accepted["mapping"][accepted["cert_id"]]["is_default"])
        self.verified(bundle, fingerprint)

    def reimport(self):
        self.current_accepted()
        good = self.load("good.json")
        bundle = self.saved_bundle(good)
        self.replace(bundle, good["sha256"])

    def rollback(self):
        self.current_accepted()
        previous = self.load("previous.json")
        bundle = self.saved_bundle(previous)
        # Keep previous intact even on failure; rollback never revokes or issues.
        self.replace(bundle, previous["sha256"])

    def watchdog(self):
        self.current_accepted()
        success = self.load("last-verified-success.json")
        age = time.time() - success.get("at", 0)
        require(0 <= age <= FRESHNESS, "Last fully verified renewal check is older than 26 hours")
        fingerprint = success.get("sha256", "")
        require(isinstance(fingerprint, str) and re.fullmatch(r"[a-f0-9]{64}", fingerprint),
                "Last verified certificate fingerprint is invalid")
        self.live(fingerprint)

    def execute(self, action):
        os.umask(0o077)
        self.audit()
        if action in ("inspect", "watchdog"):
            getattr(self, action)()
            return
        with self.lock():
            self.audit()
            for name in ("state", "certs", "issued", "bundles"):
                directory = self.path(name)
                directory.mkdir(mode=0o700, exist_ok=True)
                self.guard(directory, directory=True)
            getattr(self, action.replace("-", "_"))()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("inspect", "issue", "import-new", "accept", "run", "reimport", "rollback", "watchdog"))
    args = parser.parse_args()
    try:
        Runner().execute(args.action)
    except Failure as error:
        print("Athena ACME: " + str(error), file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError, TypeError, UnicodeError, ssl.SSLError):
        # No traceback, argv, paths from API output, or credential-bearing output.
        print("Athena ACME: operation failed; inspect protected state and DSM certificate status", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
