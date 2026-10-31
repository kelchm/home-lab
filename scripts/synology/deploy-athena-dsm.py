#!/usr/bin/env python3
"""acme.sh reload command: import Athena's installed files through DSM locally.

Run as root. acme.sh owns issuance, files and retry bookkeeping; this hook owns
only the same-ID import. No DSM login or certificate creation is performed.
"""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path("/volume1/@athena-acme")
HOST = "athena.home.kelch.io"
CERT_ID = "N2RhLk"
DESCRIPTION = "athena-dsm-acme"
API = "/usr/syno/bin/synowebapi"


def dsm(method, **parameters):
    api = "SYNO.Core.Certificate.CRT" if method == "list" else "SYNO.Core.Certificate"
    args = [API, "--exec", "api=" + api, "method=" + method, "version=1"]
    args += [key + "=" + json.dumps(value) for key, value in parameters.items()]
    result = subprocess.run(args, check=True, capture_output=True, timeout=60)
    response = json.loads(result.stdout)
    if response.get("success") is not True:
        raise RuntimeError("DSM certificate API did not report success")
    return response.get("data", {})


def check_target():
    matches = [cert for cert in dsm("list")["certificates"] if cert.get("desc") == DESCRIPTION]
    if len(matches) != 1:
        raise RuntimeError("Expected one athena-dsm-acme certificate")
    cert = matches[0]
    services = [(item["owner"], item["subscriber"], item["service"]) for item in cert["services"]]
    if (cert["id"] != CERT_ID or cert["is_default"] is not True or cert.get("is_broken") is not False
            or services != [("root", "system", "default")]):
        raise RuntimeError("DSM certificate identity or DSM-only/default binding changed")


def deploy():
    if os.geteuid() != 0:
        raise RuntimeError("Run the DSM import hook as root")
    os.umask(0o077)
    issued = ROOT / "issued"
    subprocess.run(["/usr/bin/openssl", "verify", "-purpose", "sslserver", "-verify_hostname", HOST,
                    "-untrusted", str(issued / "chain.pem"), str(issued / "cert.pem")],
                   check=True, capture_output=True, timeout=30)
    check_target()
    # DSM can consume/move these paths. Leave acme.sh's installed files intact.
    with tempfile.TemporaryDirectory(prefix="dsm-import-", dir=str(ROOT)) as stage:
        parameters = {"id": CERT_ID, "desc": DESCRIPTION, "as_default": "true"}
        for field, name in (("key_tmp", "key.pem"), ("cert_tmp", "cert.pem"), ("inter_cert_tmp", "chain.pem")):
            target = Path(stage) / name
            shutil.copyfile(issued / name, target)
            parameters[field] = str(target)
        dsm("import", **parameters)
    check_target()


if __name__ == "__main__":
    deploy()
