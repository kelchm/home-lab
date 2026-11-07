"""Adapt historical 0750 fixtures to the corrected sampled-frame and warm-ownership contract."""
from pathlib import Path
import re
import shutil

HERE = Path(__file__).resolve().parent


def optional_library(text):
    return re.sub(r'(^test "video prepare real:[^\n]+\{\n)',
                  r'\1    if (api.tfvideo.version() == null) return error.SkipZigTest;\n', text, flags=re.M)


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError('The historical video fixture changed: ' + old[:80])
    return text.replace(old, new)


def install(scratch):
    server = scratch / 'zig/src/server'
    core = server / 'video_prepare_core_test.zig'
    text = core.read_text()
    text = replace_once(text, 'truncated unsampled tail passes old eager sampling but fails full preparation',
                        'sampled frames retain Python compatibility without requiring an unsampled tail')
    text = replace_once(text, '    try t.expectError(error.Short, video.prepareLazy(arena.allocator(), src, config, 16384, 128));', '''    const prepared = try video.prepareLazy(arena.allocator(), src, config, 16384, 128);
    try t.expectEqual(old.groups.len, prepared.groups.len);
    for (prepared.groups, old.groups) |group, ref| {
        const pixels = try image(group).materialize(t.allocator);
        defer t.allocator.free(pixels);
        try t.expectEqualSlices(f32, ref.pixels, pixels);
    }''')
    text = replace_once(text, 'VIDEO_TRUNCATED old_sampled_pairs=2 preparation=Short engine_admissions=0',
                        'VIDEO_TRUNCATED sampled_pairs=2 native_eager_pixels_equal=true unsampled_tail_required=false')
    core.write_text(text + '\n' + optional_library((HERE / 'core_cases.zig').read_text()))
    http = server / 'video_prepare_http_test.zig'
    text = http.read_text()
    text = replace_once(text, 'clip[0 .. clip.len / 2], @embedFile("video-fixtures/truncated-tail.mp4"),', 'clip[0 .. clip.len / 2],')
    text = replace_once(text, 'data URL warm passes decode once on the parent thread and retain its admission',
                        'data URL warm passes borrow parent canvases without decoding and retain admission')
    start = text.index('test "video prepare real: data URL warm passes')
    end = text.index('\ntest ', start + 1)
    warm = text[start:end]
    check = 'try t.expectEqual(@as(u32, 2), e.decode_calls.load(.acquire));'
    if warm.count(check) != 2:
        raise ValueError('The historical warm fixture changed')
    warm = warm.replace(check, 'try t.expectEqual(@as(u32, 1), e.decode_calls.load(.acquire));')
    warm = warm.replace('VIDEO_WARM parent_decode=1 warm_decode=1', 'VIDEO_WARM parent_decode=1 warm_decode=0')
    text = text[:start] + warm + text[end:]
    http.write_text(text + '\n' + optional_library((HERE / 'server_cases.zig').read_text()))
    shutil.copytree(HERE / 'fixtures', server / 'video-fixtures', dirs_exist_ok=True)
