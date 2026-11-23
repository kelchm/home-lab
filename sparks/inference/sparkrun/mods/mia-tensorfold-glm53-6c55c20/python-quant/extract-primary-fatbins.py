"""Restore the verified kernel build inputs from the primary matched-quant native executable."""
import argparse
import hashlib
import struct
from pathlib import Path

ap=argparse.ArgumentParser()
ap.add_argument("binary", type=Path)
ap.add_argument("manifest", type=Path, help="two-column SHA256SUMS file for the 123 named kernel archives")
ap.add_argument("directory", type=Path)
a=ap.parse_args()
want={}
for line in a.manifest.read_text().splitlines():
    digest,name=line.split()
    if Path(name).name != name or not name.endswith(".fatbin") or len(digest)!=64 or digest in want:
        raise ValueError(line)
    want[digest]=name
if len(want)!=123:raise ValueError("Expected exactly 123 unique CUDA kernel archives")
a.directory.mkdir(parents=True,exist_ok=True)
data=a.binary.read_bytes()
cursor=0
while cursor<len(data):
    pos=data.find(struct.pack("<I",0xba55ed50),cursor)
    if pos<0:break
    _,version,header,size=struct.unpack_from("<IHHQ",data,pos)
    if version!=1 or header!=16 or not (0<size<=len(data)-pos-header):
        cursor=pos+4
        continue
    fatbin=data[pos:pos+header+size]
    cursor=pos+header+size
    digest=hashlib.sha256(fatbin).hexdigest()
    if digest in want:
        (a.directory/want.pop(digest)).write_bytes(fatbin)
if want:raise ValueError(f"Missing archives {list(want.values())}")
(a.directory/"SHA256SUMS").write_bytes(a.manifest.read_bytes())
print("Restored all 123 verified kernel archives")
