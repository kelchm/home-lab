#!/usr/bin/env python3
"""Generate encoded inputs with exact Pillow/libjpeg pins; never use native output as an oracle."""
from __future__ import annotations
import argparse
import base64
import ctypes as C
import hashlib
import io
import json
from pathlib import Path
from PIL import Image, ImageOps, features

PILLOW = '12.3.0'
JPEG = '3.1.4.1'
WEBP = '1.6.0'


def require_reference() -> None:
    found = (Image.__version__, features.version('libjpeg_turbo'), features.version('webp'))
    if found != (PILLOW, JPEG, WEBP):
        raise RuntimeError(f'reference must be Pillow/{PILLOW}, jpeg/{JPEG}, webp/{WEBP}; found {found}')


def reference(data: bytes) -> dict:
    """Production picture semantics: first MPO image, transpose EXIF, alpha over white, RGB."""
    try:
        with Image.open(io.BytesIO(data)) as opened:
            metadata = dict(format=opened.format, mode=opened.mode,
                            frames=getattr(opened, 'n_frames', 1), adobe_transform=opened.info.get('adobe_transform'))
            w, h = opened.size
            if max(w, h) > 8192 or w*h > 16 << 20:
                return dict(error='Oversized', **metadata)
            if opened.format != 'MPO' and getattr(opened, 'n_frames', 1) != 1:
                return dict(error='Animated', **metadata)
            im = ImageOps.exif_transpose(opened)
            if im.mode in ('RGBA', 'LA') or 'transparency' in im.info:
                rgba = im.convert('RGBA')
                im = Image.new('RGB', rgba.size, 'white')
                im.paste(rgba, mask=rgba.getchannel('A'))
            rgb = im.convert('RGB').tobytes()
            return dict(width=im.width, height=im.height, rgb_b64=b64(rgb),
                        rgb_sha256=sha(rgb), **metadata)
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
        return dict(error='Invalid')


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode('ascii')


def encoded(im: Image.Image, fmt: str, **kw) -> bytes:
    out = io.BytesIO()
    im.save(out, fmt, **kw)
    return out.getvalue()


def pattern(mode: str, w: int = 37, h: int = 23) -> Image.Image:
    n = len(mode) if mode != 'L' else 1
    return Image.frombytes(mode, (w, h), bytes(
        (x*43 + y*71 + c*59 + x*y*(c+1)) % 256
        for y in range(h) for x in range(w) for c in range(n)))


def no_adobe(data: bytes) -> bytes:
    """Remove only APP14, without modifying the compressed component values."""
    assert data[:2] == b'\xff\xd8'
    out, at = bytearray(data[:2]), 2
    while at < len(data):
        marker = data[at:at+2]
        if marker == b'\xff\xda':
            return bytes(out) + data[at:]
        assert marker[0] == 255
        end = at + 2 + int.from_bytes(data[at+2:at+4], 'big')
        if marker != b'\xff\xee':
            out.extend(data[at:end])
        at = end
    raise ValueError('no JPEG scan')


def turbo_cmyk(lib: Path, ycck: bool, progressive: bool) -> bytes:
    """Independent direct codec encoder, with non-inverted input (unlike Pillow's CMYK;I writer)."""
    assert b'libjpeg-turbo version 3.1.4.1 ' in lib.read_bytes(), 'wrong fixture encoder pin'
    dll = C.CDLL(str(lib))
    dll.tj3Init.argtypes, dll.tj3Init.restype = [C.c_int], C.c_void_p
    dll.tj3Set.argtypes = [C.c_void_p, C.c_int, C.c_int]
    dll.tj3Destroy.argtypes = [C.c_void_p]
    dll.tj3Free.argtypes = [C.c_void_p]
    dll.tj3Compress8.argtypes = [C.c_void_p, C.c_void_p, C.c_int, C.c_int, C.c_int, C.c_int,
                                C.POINTER(C.c_void_p), C.POINTER(C.c_size_t)]
    im = pattern('CMYK')
    h = dll.tj3Init(0)
    if not h:
        raise RuntimeError('tj3Init failed')
    out, size = C.c_void_p(), C.c_size_t()
    try:
        # QUALITY, SUBSAMP, COLORSPACE, PROGRESSIVE. TJCS_CMYK=3, TJCS_YCCK=4, TJPF_CMYK=11.
        for param, value in ((3, 93), (4, 2 if ycck else 0), (8, 4 if ycck else 3), (12, int(progressive))):
            assert dll.tj3Set(h, param, value) == 0, (param, value)
        assert dll.tj3Compress8(h, im.tobytes(), im.width, 0, im.height, 11, C.byref(out), C.byref(size)) == 0
        return C.string_at(out, size.value)
    finally:
        if out.value:
            dll.tj3Free(out)
        dll.tj3Destroy(h)


def generate(jpeg: Path, pillow_source: Path) -> dict:
    require_reference()
    cases = []

    def add(name: str, data: bytes, provenance: str) -> None:
        cases.append(dict(name=name, encoded_b64=b64(data), encoded_sha256=sha(data),
                          provenance=provenance, **reference(data)))

    rgb, gray, cmyk = pattern('RGB'), pattern('L'), pattern('CMYK')
    for subsampling in (0, 1, 2):
        for progressive in (False, True):
            add(f'rgb_s{subsampling}_progressive_{progressive}',
                encoded(rgb, 'JPEG', quality=83, subsampling=subsampling, progressive=progressive), 'Pillow encoder')
    add('gray_baseline', encoded(gray, 'JPEG', quality=91), 'Pillow encoder')
    add('gray_progressive', encoded(gray, 'JPEG', quality=91, progressive=True), 'Pillow encoder')
    adobe = encoded(cmyk, 'JPEG', quality=93)
    add('cmyk_adobe_inverted', adobe, 'Pillow CMYK;I encoder; Adobe transform=0')
    add('cmyk_inverted_no_adobe', no_adobe(adobe), 'same compressed samples; APP14 removed')
    direct = turbo_cmyk(jpeg, False, False)
    add('cmyk_noninverted_no_adobe', no_adobe(direct), 'direct libjpeg-turbo CMYK encoder; APP14 removed; Pillow still inverts')
    for progressive in (False, True):
        add(f'ycck_progressive_{progressive}', turbo_cmyk(jpeg, True, progressive), 'direct libjpeg-turbo YCCK encoder; Adobe transform=2')
    # Real independently-authored YCCK input, not an output of the implementation or either fixture encoder.
    sample = pillow_source / 'Tests/images/pil_sample_cmyk.jpg'
    data = sample.read_bytes()
    assert sha(data) == '1086f923d60bb5d6c4f2d3f7f56ba5d315d98e6f19ee8c8797dabac630c8ae80', sha(data)
    add('independent_pillow_ycck', data, 'Pillow 13.0.0 source archive Tests/images/pil_sample_cmyk.jpg; unmodified')
    for mode, im in (('rgb', rgb), ('gray', gray), ('cmyk', cmyk)):
        for orientation in range(1, 9):
            exif = Image.Exif()
            exif[0x112] = orientation
            add(f'{mode}_exif_{orientation}', encoded(im, 'JPEG', quality=89, exif=exif), 'Pillow encoder; EXIF orientation')
    broken = bytearray(encoded(cmyk, 'JPEG', quality=89, exif=exif))
    broken[broken.index(b'Exif\x00\x00') + 6] = ord('X')
    add('cmyk_malformed_exif_without_jfif', bytes(broken), 'malformed TIFF; Pillow JPEG open ignores it without JFIF DPI')
    jfif = b'\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00\x48\x00\x48\x00\x00'
    add('cmyk_malformed_exif_with_jfif', bytes(broken[:2]) + jfif + bytes(broken[2:]), 'same malformed TIFF; with JFIF DPI Pillow defers getexif and fails at transpose')
    for mode in ('RGB', 'RGBA', 'LA'):
        im = pattern(mode)
        add(f'png_{mode}', encoded(im, 'PNG'), 'Pillow encoder')
    palette = pattern('RGB').quantize(colors=16)
    palette.info['transparency'] = bytes(range(0, 256, 16))
    add('png_palette_alpha', encoded(palette, 'PNG'), 'Pillow encoder; per-entry alpha')
    for mode in ('RGB', 'RGBA'):
        for lossless in (False, True):
            im = pattern(mode)
            add(f'webp_{mode}_lossless_{lossless}', encoded(im, 'WEBP', lossless=lossless, quality=81), 'Pillow encoder')
    exif = Image.Exif()
    exif[0x112] = 6
    add('webp_alpha_exif_6', encoded(pattern('RGBA'), 'WEBP', lossless=True, exif=exif), 'Pillow encoder; alpha and EXIF')
    first, second = pattern('RGB', 31, 19), Image.new('RGB', (31, 19), 'magenta')
    mpo = encoded(first, 'MPO', save_all=True, append_images=[second], quality=91, exif=exif)
    add('mpo_two_first_image', mpo, 'Pillow MPO encoder; distinct second image; first EXIF orientation=6')
    assert cases[-1]['frames'] == 2 and cases[-1]['format'] == 'MPO'
    with Image.open(io.BytesIO(mpo)) as im:
        im.seek(1)
        assert sha(ImageOps.exif_transpose(im).convert('RGB').tobytes()) != cases[-1]['rgb_sha256']
    for name, data in (('rgb', encoded(rgb, 'JPEG')), ('cmyk', adobe), ('progressive', encoded(rgb, 'JPEG', progressive=True))):
        add(f'{name}_no_eoi', data[:-2], 'remove EOI: Pillow LOAD_TRUNCATED_IMAGES=False')
        add(f'{name}_cut_half', data[:len(data)//2], 'truncated compressed input')
    add('webp_truncated', encoded(rgb, 'WEBP')[:40], 'truncated compressed input')
    add('png_truncated', encoded(rgb, 'PNG')[:40], 'truncated compressed input')
    add('oversized_jpeg', encoded(Image.new('L', (8193, 1)), 'JPEG'), 'valid JPEG beyond native max_side')
    return dict(reference=dict(pillow=PILLOW, jpeg=JPEG, webp=WEBP), cases=cases)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--jpeg', required=True, type=Path)
    p.add_argument('--pillow-source', required=True, type=Path)
    p.add_argument('--out', required=True, type=Path)
    args = p.parse_args()
    args.out.write_text(json.dumps(generate(args.jpeg.resolve(), args.pillow_source.resolve()), indent=2) + '\n')


if __name__ == '__main__':
    main()
