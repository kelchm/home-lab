"""Frozen runtime13 host-fixture installer, isolated from concurrent shared runner changes."""
from pathlib import Path
import re
import shutil
MOD = Path(__file__).resolve().parents[2]


def install_tests(scratch, mod=MOD):
    server = scratch / 'zig/src/server'
    for source, target in (
        ('api/native_api_test.zig', 'native_api_test.zig'),
        ('api/reasoning_cache_test.zig', 'native_api_cache_test.zig'),
        ('media/native_media_test.zig', 'native_media_test.zig'),
    ):
        shutil.copy2(mod / 'tests' / source, server / target)
    media = server / 'native_media_test.zig'
    content = media.read_text()
    adapter = '.ctx = d.ctx, .prepare = d.prepare } else null'
    budgets = 'for (1..5) |n| try t.expectEqual(@as(u32, 16384), images.videoTokenBudget(o, n));'
    if content.count(adapter) != 1 or content.count(budgets) != 1:
        raise ValueError('The historical 030 media fixture changed; review the final contract adapter')
    # 070 adds checked lazy callbacks; 071 restores Python's pre-planning clip shares.
    content = content.replace(adapter, '.ctx = d.ctx, .prepare = d.prepare, .checked = d.checked } else null')
    content = content.replace(budgets, 'for ([_]u32{ 16384, 16384, 10922, 8192 }, 1..) |cap, n| try t.expectEqual(cap, images.videoTokenBudget(o, n));')
    content = content.replace('GLM video clips have independent 16384 ceilings and actual rows share 32768',
                              'GLM video clips share 32768 within their 16384 ceilings')
    # The preparation HTTP harness consumes the same real template/PNG fixtures.
    content = re.sub(r'^(const|fn) ', r'pub \1 ', content, flags=re.M)
    content = content.replace('    fn init(', '    pub fn init(').replace('    fn deinit(', '    pub fn deinit(')
    media.write_text(content + '\n' + (mod / 'tests/compat/server_cases.zig').read_text())
    for name in ('api', 'media'):
        shutil.copy2(mod / 'chat_template.jinja', server / f'native_{name}_template.jinja')
    shutil.copytree(mod / 'tests/media/fixtures', server / 'media-fixtures')
    (scratch / 'zig/src/glm_media_cpu.zig').write_text(
        'pub const picture = @import("core/picture/picture.zig");\n'
        'pub const Config = @import("families/glm/vision_config.zig").Config;\n'
        'pub const image = @import("families/glm/image_core.zig").For(picture);\n'
        'pub const video = @import("families/glm/video_core.zig").For(picture);\n')
    with (server / 'root.zig').open('a') as root:
        for name in ('native_api_test.zig', 'native_api_cache_test.zig', 'native_media_test.zig'):
            root.write(f'\ntest {{ _ = @import("{name}"); }}\n')
        for name, target in (
            ('harness.zig', 'prepare_harness.zig'),
            ('server_cases.zig', 'prepare_contract_test.zig'),
            ('arena_probe.zig', 'prepare_arena_probe.zig'),
        ):
            shutil.copy2(mod / 'tests/prepare_memory' / name, server / target)
            if name != 'harness.zig':
                root.write(f'\ntest {{ _ = @import("{target}"); }}\n')
    build = scratch / 'zig/build/cuda.zig'
    content = build.read_text()
    needle = '    const server_json = @import("server.zig").addTo(b, server_tests, host, .debug);'
    if content.count(needle) != 1:
        raise ValueError('The native test build graph changed')
    addition = '''
    server_tests.addImport("glm_media", b.createModule(.{ .root_source_file = b.path("zig/src/glm_media_cpu.zig"), .target = host, .link_libc = true }));
    b.step("test-native-bundle-contract", "Composed site API, media and body policies").dependOn(&b.addRunArtifact(b.addTest(.{ .root_module = server_tests, .filters = &.{"native API parity:", "native media parity:", "HTTP body profile:", "media compatibility:", "media lifecycle:"} })).step);
    b.step("test-native-media-budget", "Active preparation time excludes queue and prefill waits").dependOn(&b.addRunArtifact(b.addTest(.{ .root_module = mods.lanes, .filters = &.{"media compatibility:"} })).step);
    b.step("test-native-prepare-memory", "Request ownership, admission and actual allocator accounting").dependOn(&b.addRunArtifact(b.addTest(.{ .root_module = server_tests, .filters = &.{"prepare memory:", "prepare arena receipt:"} })).step);
'''
    content = content.replace(needle, needle + addition)
    needle = '    @import("glm_contracts.zig").tests(b, glm, mods.lanes, cuda, mods.core);'
    if content.count(needle) != 1:
        raise ValueError('The native GLM host build graph changed')
    addition = '''
    b.step("test-native-media-lifecycle", "Bounded media rows and actual TP protocol").dependOn(&b.addRunArtifact(b.addTest(.{ .root_module = glm, .filters = &.{"media lifecycle:", "a follower's failed image admission", "128 video pairs cross", "a row budget whose", "a request with images and a video", "a splice's map"} })).step);
'''
    build.write_text(content.replace(needle, needle + addition))

