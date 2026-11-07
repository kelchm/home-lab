//! Tests the production core/picture path and compares complete pixels with an independent Pillow oracle.
const std = @import("std");
const picture = @import("core/picture/picture.zig");
const a = std.testing.allocator;

const Case = struct {
    name: []const u8,
    encoded_b64: []const u8,
    rgb_b64: ?[]const u8 = null,
    width: usize = 0,
    height: usize = 0,
    @"error": ?[]const u8 = null,
};

fn decode64(allocator: std.mem.Allocator, text: []const u8) ![]u8 {
    const decoder = std.base64.standard.Decoder;
    const out = try allocator.alloc(u8, try decoder.calcSizeForSlice(text));
    errdefer allocator.free(out);
    try decoder.decode(out, text);
    return out;
}

fn cases(allocator: std.mem.Allocator) ![]const Case {
    const text = try std.Io.Dir.cwd().readFileAlloc(std.testing.io, "photo-cases.json", allocator, .limited(4 << 20));
    return (try std.json.parseFromSliceLeaky(struct { cases: []const Case }, allocator, text, .{ .ignore_unknown_fields = true })).cases;
}

fn requireCodecs() !void {
    // No availability skip: a missing, incomplete or wrong-version library fails this suite.
    try picture.jpeg.check(std.testing.io, a);
    try picture.webp.check(std.testing.io, a);
}

test "photo parity: production decoded pixels and refusals match pinned Pillow" {
    try requireCodecs();
    var arena_state = std.heap.ArenaAllocator.init(a);
    defer arena_state.deinit();
    const arena = arena_state.allocator();
    var decoded: usize = 0;
    var refused: usize = 0;
    for (try cases(arena)) |case| {
        errdefer std.debug.print("photo case {s}\n", .{case.name});
        const bytes = try decode64(arena, case.encoded_b64);
        if (case.@"error") |expected| {
            const err = if (picture.decode(std.testing.io, a, bytes)) |ok| blk: {
                var rgb = ok;
                rgb.deinit(a);
                break :blk error.UnexpectedDecodeSuccess;
            } else |err| err;
            try std.testing.expectEqualStrings(expected, @errorName(err));
            refused += 1;
        } else {
            var rgb = try picture.decode(std.testing.io, a, bytes);
            defer rgb.deinit(a);
            const expected = try decode64(arena, case.rgb_b64.?);
            try std.testing.expectEqual(case.width, rgb.width);
            try std.testing.expectEqual(case.height, rgb.height);
            try std.testing.expectEqualSlices(u8, expected, rgb.pixels);
            decoded += 1;
        }
    }
    try std.testing.expect(decoded >= 45 and refused >= 9);
    std.debug.print("photo parity: {d} complete pixel arrays, {d} refusals, zero skips\n", .{ decoded, refused });
}

fn decodeAndFree(allocator: std.mem.Allocator, bytes: []const u8) !void {
    var rgb = try picture.decode(std.testing.io, allocator, bytes);
    rgb.deinit(allocator);
}

fn invalidAndFree(allocator: std.mem.Allocator, bytes: []const u8) !void {
    var rgb = picture.decode(std.testing.io, allocator, bytes) catch |err| switch (err) {
        error.OutOfMemory => return err,
        error.Invalid => return,
        else => return err,
    };
    rgb.deinit(allocator);
    return error.UnexpectedDecodeSuccess;
}

test "photo parity: real CMYK decoder allocation failures and metadata errors release owned buffers" {
    try requireCodecs();
    var arena_state = std.heap.ArenaAllocator.init(a);
    defer arena_state.deinit();
    const arena = arena_state.allocator();
    var checked: usize = 0;
    for (try cases(arena)) |case| {
        if (!std.mem.eql(u8, case.name, "cmyk_exif_6") and !std.mem.eql(u8, case.name, "ycck_progressive_True")) continue;
        const bytes = try decode64(arena, case.encoded_b64);
        // Includes EXIF accumulation, the 4-byte buffer and oriented output; library handles use defer.
        try std.testing.checkAllAllocationFailures(a, decodeAndFree, .{bytes});
        // Bad TIFF header is read after the codec has successfully produced four-component pixels.
        if (std.mem.indexOf(u8, bytes, "Exif\x00\x00")) |at| {
            // A JFIF density defers Pillow's getexif until exif_transpose; its malformed TIFF then fails.
            const jfif = "\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00\x48\x00\x48\x00\x00";
            const bad = try arena.alloc(u8, bytes.len + jfif.len);
            @memcpy(bad[0..2], bytes[0..2]);
            @memcpy(bad[2..][0..jfif.len], jfif);
            @memcpy(bad[2 + jfif.len ..], bytes[2..]);
            bad[at + jfif.len + 6] = 'X';
            try std.testing.checkAllAllocationFailures(a, invalidAndFree, .{bad});
        }
        checked += 1;
    }
    try std.testing.expectEqual(@as(usize, 2), checked);
}

// Include the existing picture tests in a separate regression run.
test {
    _ = picture;
}
