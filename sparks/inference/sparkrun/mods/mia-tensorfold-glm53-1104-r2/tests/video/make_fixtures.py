#!/usr/bin/env python3
"""Create a small local codec smoke set; no downloads or model execution."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--ffmpeg', default='ffmpeg')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    frames = b''.join(bytes((30 + 20 * i, 50 + 5 * i, 200 - 10 * i)) * (64 * 48) for i in range(8))
    version = subprocess.check_output([args.ffmpeg, '-version'], text=True).splitlines()[0]
    for filename, codec, options in (
        ('h264.mp4', 'libx264', ['-preset', 'ultrafast', '-crf', '0']),
        ('hevc.mp4', 'libx265', ['-x265-params', 'log-level=error:pools=1:frame-threads=1', '-crf', '0']),
        ('vp8.webm', 'libvpx', ['-b:v', '1M']),
        ('vp9.webm', 'libvpx-vp9', ['-lossless', '1']),
        ('mpeg4.mp4', 'mpeg4', ['-q:v', '1']),
    ):
        command = [args.ffmpeg, '-hide_banner', '-loglevel', 'error', '-f', 'rawvideo', '-pixel_format', 'rgb24',
                   '-video_size', '64x48', '-framerate', '4', '-i', 'pipe:0', '-frames:v', '8', '-an',
                   '-c:v', codec, '-pix_fmt', 'yuv420p', *options, str(args.output / filename)]
        subprocess.run(command, input=frames, check=True)
    manifest = {'schema': 'glm-video-decoder-smoke/v1', 'generator': version,
                'source_rgb_sha256': hashlib.sha256(frames).hexdigest(),
                'files': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(args.output.iterdir())}}
    (args.output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
