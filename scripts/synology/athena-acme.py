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
import http.client
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
import shutil
import urllib.parse
import urllib.request

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
    "dnsapi/dns_athena.sh": "789ba78a2020ebe20ea4d8b8de6bd7fb09bda123acae29aaf7cef085d8fa6c5e",
}
FILES = ("cert.pem", "key.pem", "chain.pem", "fullchain.pem")
MIN_VALIDITY = 7 * 86400
RECOVERY_VALIDITY = 86400
FRESHNESS = 3 * 3600
MAX_OUTPUT = 1024 * 1024


class Failure(Exception):
    """A deliberately sanitized operator-facing failure."""


def require(condition, message):
    if not condition:
        raise Failure(message)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def parse_api(output):
    """Parse stdout only; native diagnostics are captured separately."""
    try:
        text = output.decode("utf-8")
        result = json.loads(text)
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
            require(type(cert.get("is_broken")) is bool, "DSM certificate health metadata is invalid")
            if cert["desc"] == DESCRIPTION:
                require(not cert["is_broken"], "Managed DSM certificate is broken")
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
        self.command_stderr = b""

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
            self.sync_directory(path.parent)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def sync_directory(self, path):
        self.guard(path, directory=True)
        descriptor = os.open(str(path), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

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
        require(actual == set(SOURCE_HASHES), "Only the pinned ACME core and Athena DNS adapter may be installed")
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
                        expected = {"Le_Domain": HOST, "Le_Alt": "no", "Le_Webroot": "dns_athena",
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
        self.write(self.path("last-error.log"), (output[:32768] + b"\n--- stderr ---\n" + self.command_stderr[:32768]))

    def command(self, argv, env=None, timeout=120):
        # Outputs may contain Cloudflare credentials. Keep them in an unlinked,
        # root-only temporary file; never include command/output in exceptions.
        environment = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": "/root", "LC_ALL": "C"}
        if env:
            environment.update(env)
        self.command_stderr = b""
        with tempfile.TemporaryFile(dir=str(self.root)) as output, tempfile.TemporaryFile(dir=str(self.root)) as errors:
            process = None
            completed = False
            interrupted = None
            previous_handlers = {}

            def requested_stop(signum, frame):
                nonlocal interrupted
                # Do not raise inside Popen before it returns the child PID.
                interrupted = signum

            try:
                for signum in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
                    previous_handlers[signum] = signal.signal(signum, requested_stop)
                if interrupted is not None:
                    raise Failure("External command interrupted by a termination signal")
                process = subprocess.Popen(argv, stdout=output, stderr=errors,
                                           env=environment, start_new_session=True)
                deadline = time.monotonic() + timeout
                while process.poll() is None:
                    if interrupted is not None:
                        raise Failure("External command interrupted by a termination signal")
                    if time.monotonic() >= deadline or sum(os.fstat(stream.fileno()).st_size for stream in (output, errors)) > MAX_OUTPUT:
                        raise Failure("External command timed out or exceeded its output limit")
                    try:
                        process.wait(timeout=0.1)
                    except subprocess.TimeoutExpired:
                        pass
                if interrupted is not None:
                    raise Failure("External command interrupted by a termination signal")
                output.seek(0)
                errors.seek(0)
                data = output.read(MAX_OUTPUT + 1)
                self.command_stderr = errors.read(MAX_OUTPUT + 1)
                if len(data) + len(self.command_stderr) > MAX_OUTPUT:
                    raise Failure("External command exceeded its output limit")
                completed = True
                return process.returncode, data
            finally:
                try:
                    if process is not None and not completed:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        process.wait()  # Complete cleanup before lock() can release.
                    if not completed:
                        output.seek(0)
                        errors.seek(0)
                        self.command_stderr = errors.read(32768)
                        self.diagnostic(output.read(32768))
                finally:
                    for signum, previous in previous_handlers.items():
                        signal.signal(signum, previous)

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
        cert_id = accepted.get("cert_id")
        self.standing_identity(current, cert_id)
        mapping = accepted.get("mapping")
        require(isinstance(mapping, dict) and current[cert_id] == mapping.get(cert_id),
                "Managed DSM certificate metadata or service assignments drifted")
        # Unrelated objects may change between runs. Return the current full map
        # so each import can still prove it preserved the bindings it found.
        return {"cert_id": cert_id, "mapping": current}

    def standing_identity(self, current, cert_id):
        require(named_certificate(current) == cert_id, "Recorded DSM certificate is missing or changed")
        require(current[cert_id]["is_default"] and any(
            tuple(service[key] for key in ("owner", "subscriber", "service")) == ("root", "system", "default")
            for service in current[cert_id]["services"]), "Recorded certificate must remain DSM system/default")

    def review_bindings(self):
        accepted = self.load("accepted.json")
        current = self.snapshot()
        self.standing_identity(current, accepted.get("cert_id"))
        print(json.dumps({"accepted": accepted["mapping"], "current": current,
                          "current_sha256": hashlib.sha256(canonical(current).encode()).hexdigest()}, indent=2, sort_keys=True))

    def rebaseline(self, expected_snapshot):
        require(bool(re.fullmatch(r"[a-f0-9]{64}", expected_snapshot or "")),
                "Rebaseline requires the reviewed current map SHA-256")
        accepted = self.load("accepted.json")
        current = self.snapshot()
        self.standing_identity(current, accepted.get("cert_id"))
        require(hashlib.sha256(canonical(current).encode()).hexdigest() == expected_snapshot,
                "DSM map differs from the reviewed snapshot")
        good = self.load("good.json")
        self.saved_bundle(good, validate=False)
        self.live(good["sha256"], minimum=0)
        require(self.snapshot() == current, "DSM map changed during rebaseline")
        self.save("accepted.json", {"cert_id": accepted["cert_id"], "mapping": current})
        # Do not advance the renewal marker. Run must still issue/check normally.

    def cloudflare(self, method, endpoint, payload=None):
        body = None if payload is None else canonical(payload).encode()
        request = urllib.request.Request("https://api.cloudflare.com/client/v4/" + endpoint,
            data=body, method=method, headers={"Authorization": "Bearer " + self.token(), "Content-Type": "application/json"})
        # A default opener may follow redirects with the Authorization header.
        # Refuse redirects; the fixed Cloudflare origin must answer directly.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, request, fp, code, message, headers, newurl):
                return None
        with urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect()).open(request, timeout=30) as response:
            raw = response.read(MAX_OUTPUT + 1)
        require(len(raw) <= MAX_OUTPUT, "Cloudflare response exceeds its size limit")
        data = json.loads(raw)
        require(isinstance(data, dict) and data.get("success") is True and isinstance(data.get("result"), list if method == "GET" else dict),
                "Cloudflare DNS API did not report the expected success")
        return data["result"]

    def dns(self, action, name, value):
        require(name == "_acme-challenge." + HOST and bool(re.fullmatch(r"[A-Za-z0-9_-]{43}", value or "")),
                "DNS hook accepts only the Athena challenge name and a SHA-256 TXT value")
        if action == "dns-remove":
            # acme.sh ignores cleanup-hook exit status. Retain the exact public
            # value until deletion succeeds so the parent can retry or notify.
            self.save("dns-cleanup-pending.json", {"name": name, "value": value})
        zones = self.cloudflare("GET", "zones?name=kelch.io")
        require(len(zones) == 1 and isinstance(zones[0], dict) and zones[0].get("name") == "kelch.io", "Expected exactly the kelch.io zone")
        zone = zones[0].get("id", "")
        require(isinstance(zone, str) and bool(re.fullmatch(r"[a-f0-9]{32}", zone)), "Cloudflare zone ID is invalid")
        endpoint = "zones/" + zone + "/dns_records"
        records = self.cloudflare("GET", endpoint + "?" + urllib.parse.urlencode({"type": "TXT", "name.exact": name, "match": "all", "per_page": 100}))
        require(len(records) < 100, "Ambiguous paginated challenge records require inspection")
        matches = []
        for record in records:
            require(isinstance(record, dict) and record.get("type") == "TXT" and record.get("name") == name,
                    "Unexpected challenge record response")
            if record.get("content") not in (value, '"' + value + '"'):
                continue  # Preserve another client's simultaneous TXT value.
            record_id = record.get("id", "")
            require(isinstance(record_id, str) and bool(re.fullmatch(r"[a-f0-9]{32}", record_id)), "Cloudflare record ID is invalid")
            matches.append(record_id)
        if action == "dns-add" and not matches:
            self.cloudflare("POST", endpoint, {"type": "TXT", "name": name, "content": value, "ttl": 120})
        elif action == "dns-remove":
            for record_id in matches:
                self.cloudflare("DELETE", endpoint + "/" + record_id)
            self.path("dns-cleanup-pending.json").unlink()

    def finish_dns_cleanup(self):
        if self.path("dns-cleanup-pending.json").exists():
            pending = self.load("dns-cleanup-pending.json")
            self.dns("dns-remove", pending.get("name"), pending.get("value"))

    def token(self):
        token = self.read(self.path("cloudflare-token")).decode("ascii").strip()
        require(bool(re.fullmatch(r"[A-Za-z0-9_-]{20,256}", token)), "Cloudflare credential format is invalid")
        return token

    def provision_token(self):
        data = sys.stdin.buffer.read(1025)
        token = data.strip()
        require(len(data) <= 1024 and bool(re.fullmatch(rb"[A-Za-z0-9_-]{20,256}", token)),
                "No valid Cloudflare credential received; token file was not created")
        descriptor = os.open(str(self.path("cloudflare-token")), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(token)
            stream.flush()
            os.fsync(stream.fileno())

    def acme(self, initial=False, force=False):
        require(type(force) is bool and not (force and initial), "Force must be a boolean for renewal only")
        self.token()  # Validate the protected input before starting the client.
        self.finish_dns_cleanup()
        common = ["/bin/sh", str(self.path("client/acme.sh")),
                  "--home", str(self.path("client")), "--config-home", str(self.path("state")),
                  "--cert-home", str(self.path("certs")), "--server", "letsencrypt"]
        domain_conf = self.path("certs") / (HOST + "_ecc") / (HOST + ".conf")
        if initial and not domain_conf.exists():
            action = ["--issue", "--dns", "dns_athena", "-d", HOST, "--keylength", "ec-256"]
        else:
            action = ["--renew", "-d", HOST, "--ecc"]
        if force:
            action.append("--force")
        code, output = self.command(common + action, timeout=1800)
        self.finish_dns_cleanup()
        if not (code == 0 or (code == 2 and "--renew" in action and not force)):
            self.diagnostic(output)
            raise Failure("ACME issue/renew failed")
        self.audit()
        install = ["--install-cert", "-d", HOST, "--ecc", "--cert-file", str(self.path("issued/cert.pem")),
                   "--key-file", str(self.path("issued/key.pem")), "--ca-file", str(self.path("issued/chain.pem")),
                   "--fullchain-file", str(self.path("issued/fullchain.pem"))]
        code, output = self.command(common + install, timeout=120)
        if code != 0:
            self.diagnostic(output)
            raise Failure("ACME stable certificate installation failed")
        self.audit()
        return self.bundle("issued")

    def bundle(self, directory):
        return {name: self.path(directory) / name for name in FILES}

    def validate(self, bundle, minimum=MIN_VALIDITY):
        for path in bundle.values():
            self.read(path)
        cert, key, chain = (str(bundle[name]) for name in FILES[:3])
        public_cert = self.checked([OPENSSL, "x509", "-in", cert, "-pubkey", "-noout"])
        public_key = self.checked([OPENSSL, "pkey", "-in", key, "-pubout"])
        require(public_cert == public_key, "Issued certificate does not match its private key")
        san = self.checked([OPENSSL, "x509", "-in", cert, "-noout", "-ext", "subjectAltName"])
        names = [item.strip() for item in b"\n".join(san.splitlines()[1:]).decode("ascii").split(",")]
        require(names == ["DNS:" + HOST], "Certificate must have exactly the Athena DNS SAN")
        self.checked([OPENSSL, "x509", "-in", cert, "-noout", "-checkend", str(minimum)])
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
        require(self.validate(archived, minimum=0) == fingerprint, "Archived certificate bundle is inconsistent")
        self.sync_directory(directory)
        self.sync_directory(directory.parent)
        return {"sha256": fingerprint}

    def saved_bundle(self, pointer, validate=True, minimum=MIN_VALIDITY):
        fingerprint = pointer.get("sha256", "")
        require(isinstance(fingerprint, str) and re.fullmatch(r"[a-f0-9]{64}", fingerprint),
                "Saved certificate pointer is invalid")
        bundle = self.bundle("bundles/" + fingerprint)
        if validate:
            require(self.validate(bundle, minimum=minimum) == fingerprint, "Saved certificate bundle does not match its pointer")
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
        require(type(is_default) is bool, "DSM default preservation flag must be a boolean")
        require(cert_id != ORIGINAL_ID, "Refusing to replace the original DSM certificate")
        # DSM may move the supplied paths; originals and rollback remain intact.
        with tempfile.TemporaryDirectory(prefix="import-", dir=str(self.root)) as stage:
            parameters = {"desc": DESCRIPTION}
            # DSM's native parameter checker requires a string for this field.
            # Omit it for nondefault imports; request only the known true case.
            if is_default:
                parameters["as_default"] = "true"
            for field, filename in (("key_tmp", "key.pem"), ("cert_tmp", "cert.pem"),
                                    ("inter_cert_tmp", "chain.pem")):
                target = Path(stage) / filename
                self.write(target, self.read(bundle[filename]))
                parameters[field] = str(target)
            if cert_id is not None:
                parameters["id"] = cert_id
            return self.api("import", parameters)

    def verified(self, bundle, fingerprint, minimum=MIN_VALIDITY):
        self.live(fingerprint, minimum=minimum)
        self.current_accepted()
        pointer = self.archive(bundle, fingerprint)
        self.save("good.json", pointer)
        keep = {fingerprint}
        if self.path("previous.json").exists():
            previous = self.load("previous.json")
            self.saved_bundle(previous, validate=False)
            keep.add(previous["sha256"])
        for directory in self.path("bundles").iterdir():
            if re.fullmatch(r"[a-f0-9]{64}", directory.name) and directory.name not in keep:
                self.guard(directory, directory=True)
                shutil.rmtree(str(directory))
        self.sync_directory(self.path("bundles"))
        self.save("last-verified-success.json", {"at": int(time.time()), "sha256": fingerprint})

    def inspect(self):
        current = self.snapshot()
        print(json.dumps({"hostname": HOST, "certificates": current,
                          "current_sha256": hashlib.sha256(canonical(current).encode()).hexdigest()}, indent=2, sort_keys=True))

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
            data = response.get("data")
            require(isinstance(data, dict), "DSM import response data is invalid")
            returned_id = data.get("id")
            require(returned_id == cert_id, "DSM import response does not identify the new certificate")
        else:
            # Recover a successful import whose response/postcheck was interrupted.
            cert_id = named_certificate(current)
        expected = dict(baseline)
        expected[cert_id] = {"desc": DESCRIPTION, "is_default": False, "services": []}
        require(current == expected, "Initial import changed existing defaults or assignments")
        self.save("candidate.json", {"cert_id": cert_id, "sha256": fingerprint})
        print("Initial certificate imported. Bind only DSM system/default in the UI, then run accept.")

    def recover_initial(self, expected_snapshot):
        require(not self.path("accepted.json").exists(), "Initial acceptance already exists")
        require(not self.path("candidate.json").exists(), "Initial candidate already exists")
        require(isinstance(expected_snapshot, str) and bool(re.fullmatch(r"[a-f0-9]{64}", expected_snapshot)),
                "Initial recovery requires the reviewed current map SHA-256")
        baseline = self.load("baseline.json")["mapping"]
        current = self.snapshot()
        cert_id = named_certificate(current)
        accepted_transition(baseline, current, cert_id)
        require(hashlib.sha256(canonical(current).encode()).hexdigest() == expected_snapshot,
                "DSM map differs from the reviewed snapshot")
        fingerprint = self.validate(self.bundle("issued"))
        self.live(fingerprint)
        require(self.snapshot() == current, "DSM map changed during initial recovery")
        # Record only the candidate. Normal accept must independently verify and
        # publish the accepted identity, recovery bundle, and success marker.
        self.save("candidate.json", {"cert_id": cert_id, "sha256": fingerprint})
        print("Initial candidate recovered without import or binding changes. Run accept.")

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

    def renew_now(self, expected_fingerprint):
        require(isinstance(expected_fingerprint, str) and bool(re.fullmatch(r"[a-f0-9]{64}", expected_fingerprint)),
                "Supervised renewal requires the reviewed current good leaf SHA-256")
        accepted = self.current_accepted()
        good = self.load("good.json")
        require(good.get("sha256") == expected_fingerprint, "Current good leaf differs from the reviewed fingerprint")
        self.saved_bundle(good, minimum=RECOVERY_VALIDITY)
        require(self.validate(self.bundle("issued")) == expected_fingerprint,
                "Issued leaf differs from good; use ordinary run to recover any pending import before renew-now")
        self.live(expected_fingerprint)
        require(self.current_accepted() == accepted, "DSM map changed during supervised renewal precheck")
        self.run(force=True)

    def run(self, force=False):
        require(type(force) is bool, "Force must be a boolean")
        self.current_accepted()
        bundle = self.acme(force=True) if force else self.acme()
        fingerprint = self.validate(bundle)
        good = self.load("good.json")
        require(not force or fingerprint != good.get("sha256"), "Forced renewal did not produce a different leaf")
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

    def replace(self, bundle, fingerprint, minimum=MIN_VALIDITY):
        accepted = self.current_accepted()
        self.import_bundle(bundle, accepted["cert_id"], accepted["mapping"][accepted["cert_id"]]["is_default"])
        deadline = time.monotonic() + 60
        while True:
            try:
                self.live(fingerprint, minimum=minimum)
                break
            except (Failure, OSError, ValueError):
                if time.monotonic() >= deadline:
                    raise Failure("DSM did not serve the expected trusted leaf within 60 seconds")
                time.sleep(2)
        require(self.current_accepted() == accepted, "DSM certificate assignments changed during import")
        self.verified(bundle, fingerprint, minimum=minimum)

    def reimport(self):
        self.current_accepted()
        good = self.load("good.json")
        bundle = self.saved_bundle(good, minimum=RECOVERY_VALIDITY)
        self.replace(bundle, good["sha256"], minimum=RECOVERY_VALIDITY)

    def rollback(self):
        self.current_accepted()
        previous = self.load("previous.json")
        bundle = self.saved_bundle(previous, minimum=RECOVERY_VALIDITY)
        # Keep previous intact even on failure; rollback never revokes or issues.
        self.replace(bundle, previous["sha256"], minimum=RECOVERY_VALIDITY)

    def watchdog(self):
        self.current_accepted()
        success = self.load("last-verified-success.json")
        age = time.time() - success.get("at", 0)
        require(0 <= age <= FRESHNESS, "Last fully verified renewal check is older than 3 hours")
        fingerprint = success.get("sha256", "")
        require(isinstance(fingerprint, str) and re.fullmatch(r"[a-f0-9]{64}", fingerprint),
                "Last verified certificate fingerprint is invalid")
        self.live(fingerprint)

    def execute(self, action, arguments=()):
        os.umask(0o077)
        self.audit()
        if action in ("dns-add", "dns-remove"):
            self.dns(action, *arguments)
            return
        if action in ("inspect", "watchdog", "review-bindings"):
            getattr(self, action.replace("-", "_"))()
            return
        with self.lock():
            self.audit()
            for name in ("state", "certs", "issued", "bundles"):
                directory = self.path(name)
                directory.mkdir(mode=0o700, exist_ok=True)
                self.guard(directory, directory=True)
            getattr(self, action.replace("-", "_"))(*arguments)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("inspect", "provision-token", "issue", "import-new", "recover-initial", "accept", "run", "renew-now", "reimport", "rollback", "watchdog", "review-bindings", "rebaseline", "dns-add", "dns-remove"))
    parser.add_argument("arguments", nargs="*")
    args = parser.parse_args()
    count = 2 if args.action in ("dns-add", "dns-remove") else 1 if args.action in ("rebaseline", "recover-initial", "renew-now") else 0
    if len(args.arguments) != count:
        parser.error("Incorrect argument count for " + args.action)
    try:
        Runner().execute(args.action, args.arguments)
    except Failure as error:
        print("Athena ACME: " + str(error), file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError, TypeError, AttributeError, UnicodeError, ssl.SSLError, http.client.HTTPException):
        # No traceback, argv, paths from API output, or credential-bearing output.
        print("Athena ACME: operation failed; inspect protected state and DSM certificate status", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
