"""Recover a qualified OCI manifest from a pinned parent, rebuilt files and their original layer metadata."""
import argparse
import base64
import gzip
import hashlib
import io
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile


HERE = Path(__file__).resolve().parent
FILES = {"opt/tensorfold/native/bin/tensorfold-native", "opt/tensorfold/BUILD-INFO"}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def encode(data):
    return base64.b64encode(data).decode("ascii")


def decode(encoded):
    return base64.b64decode(encoded)


def file_entry(archive, name, data):
    entry = tarfile.TarInfo(name)
    entry.size = len(data)
    archive.addfile(entry, io.BytesIO(data))


def capture(overlay, destination):
    """Record OCI metadata. The ELF, build receipt and compressed filesystem layers are excluded."""
    with tarfile.open(overlay) as source:
        identity = json.load(source.extractfile("overlay.json"))
        layer_paths = identity["layers_paths"][len(identity["base_paths"]):]
        metadata = {m.name: encode(source.extractfile(m).read()) for m in source
                    if m.isfile() and m.name not in layer_paths and m.name != "overlay.json"}
        layers = []
        for path in layer_paths:
            compressed = source.extractfile(path).read()
            if path.split("/")[-1] != sha(compressed):
                raise ValueError(f"Layer is not content-addressed: {path}")
            raw = gzip.decompress(compressed)
            with tarfile.open(fileobj=io.BytesIO(raw)) as layer:
                item, = [m for m in layer if m.isfile()]
                if item.name not in FILES:
                    raise ValueError(f"Unexpected file in release layer: {item.name}")
                end = item.offset_data + item.size
                layers.append(dict(path=path, file=item.name, sha256=sha(raw[item.offset_data:end]),
                                   diff_id="sha256:" + sha(raw), prefix=encode(raw[:item.offset_data]), suffix=encode(raw[end:])))
        if {layer["file"] for layer in layers} != FILES:
            raise ValueError("Expected an executable and its build receipt")
        config = json.loads(decode(metadata[identity["config_path"]]))
        if config["rootfs"]["diff_ids"] != identity["base_layers"] + [layer["diff_id"] for layer in layers]:
            raise ValueError("Original layer identities do not match config")
        record = dict(base_config=identity["base_config"], base_paths=identity["base_paths"], layers=layers, metadata=metadata)
        destination.write_bytes(gzip.compress(json.dumps(record, separators=(",", ":")).encode(), compresslevel=9, mtime=0))


def restore_nvcc_names(target, expected):
    """Restore two internal CUDA C++ symbol names when that alone recovers the exact qualified binary."""
    data = target.read_bytes()
    names = [(b"_ZN40_INTERNAL_481b65f4_6_qmm_cu_b8f42a2c_", b"851"),
             (b"_ZN48_INTERNAL_ce744d04_12_qmm_group_cu_b8f42a2c_", b"1616")]
    suffix = b"4cuda3std3__45__cpo9iter_swapE"
    for prefix, number in names:
        expression = re.escape(prefix) + rb"[0-9]+" + re.escape(suffix)
        if len(re.findall(expression, data)) != 1:
            return False
        data = re.sub(expression, prefix + number + suffix, data)
    if sha(data) != expected:
        return False
    target.write_bytes(data)
    return True


def built_files(image, work, layers):
    container = subprocess.check_output(["docker", "create", "--entrypoint=/bin/true", image], text=True).strip()
    try:
        for layer in layers:
            target = work / Path(layer["file"]).name
            subprocess.run(["docker", "cp", f"{container}:/{layer['file']}", str(target)], check=True)
            with target.open("rb") as f:
                digest = hashlib.file_digest(f, "sha256").hexdigest()
            if digest != layer["sha256"]:
                if target.name == "tensorfold-native" and restore_nvcc_names(target, layer["sha256"]):
                    print("Recovered the qualified ELF by restoring two internal CUDA symbol names.", flush=True)
                    continue
                raise ValueError(f"Rebuilt {target.name} has sha256 {digest}, expected {layer['sha256']}")
    finally:
        subprocess.run(["docker", "rm", container], check=True, stdout=subprocess.DEVNULL)


def compress_layers(work, layers, go):
    version = subprocess.check_output([go, "version"], text=True).split()
    if len(version) < 3 or not version[2].startswith("go1.26."):
        raise ValueError("The reference Docker writer uses Go 1.26 gzip; pass its go executable via --go")
    compressor = work / "gzip-layer"
    subprocess.run([go, "build", "-o", str(compressor), str(HERE / "gzip-layer.go")], check=True,
                   env={**os.environ, "GO111MODULE": "off", "GOTOOLCHAIN": "local"})
    for layer in layers:
        packed = work / layer["path"].split("/")[-1]
        with packed.open("wb") as out, subprocess.Popen([str(compressor)], stdin=subprocess.PIPE, stdout=out) as process:
            with process.stdin, (work / Path(layer["file"]).name).open("rb") as payload:
                process.stdin.write(decode(layer["prefix"]))
                shutil.copyfileobj(payload, process.stdin)
                process.stdin.write(decode(layer["suffix"]))
            if process.wait():
                raise RuntimeError("Layer compression failed")
        with packed.open("rb") as f:
            actual = hashlib.file_digest(f, "sha256").hexdigest()
        if actual != packed.name:
            raise ValueError(f"Compressed {layer['file']} has sha256 {actual}, expected {packed.name}")


def verify_record(record_path, expected_config, expected_manifest):
    record = json.loads(gzip.decompress(record_path.read_bytes()))
    metadata = {name: decode(value) for name, value in record["metadata"].items()}
    platform, = json.loads(metadata["index.json"])["manifests"]
    if platform["digest"] != expected_manifest:
        raise ValueError("Recorded platform manifest differs from the variant lock")
    manifest = metadata["blobs/sha256/" + expected_manifest.removeprefix("sha256:")]
    descriptor = json.loads(manifest)
    if sha(manifest) != expected_manifest[7:] or descriptor["config"]["digest"] != expected_config:
        raise ValueError("Recorded configuration or manifest differs from the variant lock")
    if sha(metadata["blobs/sha256/" + expected_config[7:]]) != expected_config[7:]:
        raise ValueError("Recorded image configuration is corrupt")
    return record, metadata


def restore(record_path, candidate, parent, go, expected_config, expected_manifest):
    record, metadata = verify_record(record_path, expected_config, expected_manifest)
    with tempfile.TemporaryDirectory() as temp:
        work = Path(temp)
        built_files(candidate, work, record["layers"])
        compress_layers(work, record["layers"], go)
        required = set(record["base_paths"])
        with subprocess.Popen(["docker", "image", "save", "--platform", "linux/arm64", parent], stdout=subprocess.PIPE) as export, \
             subprocess.Popen(["docker", "load"], stdin=subprocess.PIPE) as load:
            with load.stdin, tarfile.open(mode="w|", bufsize=1024 * 1024, copybufsize=1024 * 1024, fileobj=load.stdin) as archive:
                for name, contents in metadata.items():
                    file_entry(archive, name, contents)
                for layer in record["layers"]:
                    archive.add(work / layer["path"].split("/")[-1], arcname=layer["path"])
                with tarfile.open(mode="r|", bufsize=1024 * 1024, fileobj=export.stdout) as original:
                    for item in original:
                        if item.isfile() and item.name in required:
                            archive.addfile(item, original.extractfile(item))
                            required.remove(item.name)
                if required or export.wait():
                    raise ValueError("Docker did not export every pinned parent OCI layer")
            if load.wait():
                raise RuntimeError("Docker rejected reconstructed OCI image")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Capture timestamp and header records for a qualified OCI variant.")
    parser.add_argument("overlay", type=Path)
    parser.add_argument("record", type=Path)
    args = parser.parse_args()
    capture(args.overlay, args.record)
