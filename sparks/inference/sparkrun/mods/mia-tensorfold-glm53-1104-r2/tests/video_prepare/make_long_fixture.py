#!/usr/bin/env python3
"""Generate a bounded 33-second 1080p moving fixture outside the repository."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--output', type=Path, required=True)
p.add_argument('--ffmpeg', default='ffmpeg')
a = p.parse_args()
if a.output.exists():
    p.error('output must be new')
cmd = [a.ffmpeg, '-nostdin', '-hide_banner', '-loglevel', 'error', '-f', 'lavfi',
       '-i', 'testsrc2=size=1920x1080:rate=30:duration=33', '-an', '-c:v', 'libx264',
       '-preset', 'ultrafast', '-crf', '40', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', str(a.output)]
subprocess.run(cmd, check=True)
size = a.output.stat().st_size
if size > 16 << 20:
    raise RuntimeError(f'generated clip exceeds encoded cap: {size}')
receipt = {'command': cmd, 'ffmpeg': subprocess.check_output([a.ffmpeg, '-version'], text=True).splitlines()[0],
           'encoded_bytes': size, 'sha256': hashlib.sha256(a.output.read_bytes()).hexdigest()}
a.output.with_suffix('.json').write_text(json.dumps(receipt, indent=2) + '\n')
print(json.dumps(receipt))
