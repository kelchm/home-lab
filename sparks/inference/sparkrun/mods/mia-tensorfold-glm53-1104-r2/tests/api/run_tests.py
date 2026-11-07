#!/usr/bin/env python3
"""Exercise 020 native API parity patches against a disposable copy of a patched engine.

Usage:
    python3 tests/api/run_tests.py --engine /path/to/patched106/engine --zig /path/to/zig --full

Requires Zig 0.17.0. This script never clones/fetches, writes to the supplied engine,
changes a Git branch, or contacts a model host. It copies native sources into a new
scratch directory, checks/applies only this mod's 020*.patch files, installs the
standalone Zig tests there, and runs the existing native server build graph. The
script prints its scratch path and the patch digest for the bundle's receipts.
--full also runs test-server-cpu and builds server-cpu; no GPU/model is loaded.

Cache contract implemented by 0200-native-api-parity.patch:
* One server-owned LRU entry per normally completed, successfully delivered reply.
* At most 1024 entries and 32 MiB of UTF-8 reasoning, IDs, and entry storage, which
  is stricter than Python's 32 Mi characters. The hash index has at most 131072
  keys; its bounded capacity is additional allocator bookkeeping.
* At most 128 calls/reply and 256 bytes/ID are retained. Larger replies still work
  normally but are not cached. Stream validation holds at most 32 MiB of arguments.
* Caller nonblank reasoning_content takes precedence; reasoning is normalized as
  its alias. Existing reasoning/inline </think> blocks prevent cache injection.
* Every supplied ID must exactly identify the same retained reply. A missing,
  unknown, renumbered, mixed-reply, or mismatched streamed call is a cache miss.
  There is no history, name, or argument signature lookup. Parallel calls share
  one entry and all retain their actual client-visible IDs.
* Stored strings are copied out of the generation arena. Lookup, copying into the
  next request, LRU movement, insertion and eviction hold the same std.Io.Mutex.
  Server shutdown owns destruction after connections stop.
* Length/failed/cancelled/stop-string/loop-stop replies, synthetically closed calls,
  and failed whole/stream writes cannot enter the cache. A successful write is the
  transport boundary; TCP cannot prove that the remote application consumed it.
* Unknown nonempty tool-result IDs in a block following fully identified
  assistant calls get a 400 before admission. Missing/blank IDs keep the existing
  lossless, unsorted path.

The supplied f350+patched106 base has three inherited json_answer_test failures on
this macOS/Zig 0.17 validation host: tool markers inside JSON strings, ordinary
replies beside tools, and guided_regex beside tools. They also fail with no 020
patch applied. --full reports them normally; it does not suppress known failures.

The model template is the mod's actual chat_template.jinja, loaded by HfText. The
small byte tokenizer and scripted engine replace weights/GPU execution only; all
normalization, rendering, parsing, streaming and API adapters are production Zig.
Defaults remain thinking on / clear_thinking false with no new loop/tool guard or
thinking budget. Anthropic's existing default remains unchanged; tests explicitly
request adaptive thinking there. A changed native source/patch hash requires a new
binary/image identity and invalidates any prior API qualification receipt; model
weights and arithmetic are unchanged. Live checkpoint qualification is separate.
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


def run(argv: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> None:
    print("+ " + " ".join(argv), flush=True)
    subprocess.run(argv, cwd=cwd, env=env, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--engine", required=True, type=Path, help="read-only patched106/compatible engine base")
    parser.add_argument("--zig", default="zig", help="Zig 0.17.0 executable")
    parser.add_argument("--full", action="store_true", help="also run the native server suite and compile CPU artifacts")
    parser.add_argument("--cpu", help="optional Zig -Dcpu, e.g. apple_m1")
    args = parser.parse_args()
    engine = args.engine.resolve(strict=True)
    version = subprocess.check_output([args.zig, "version"], text=True).strip()
    if version != "0.17.0":
        parser.error(f"expected Zig 0.17.0, found {version}")
    here = Path(__file__).resolve().parent
    mod = here.parent.parent
    patches = sorted((mod / "patches").glob("020*.patch"))
    if not patches:
        parser.error("no 020 API patches found")
    scratch = Path(tempfile.mkdtemp(prefix="glm-native-api-test-"))
    print(f"scratch={scratch}", flush=True)
    for name in ("zig", "tools", "build.zig", "build.zig.zon", ".zig-version"):
        source = engine / name
        if source.is_dir():
            shutil.copytree(source, scratch / name)
        else:
            shutil.copy2(source, scratch / name)
    for patch in patches:
        print(f"patch={patch.name} sha256={hashlib.sha256(patch.read_bytes()).hexdigest()}", flush=True)
        run(["git", "apply", "--check", str(patch)], cwd=scratch)
        run(["git", "apply", str(patch)], cwd=scratch)
    server = scratch / "zig/src/server"
    shutil.copy2(here / "native_api_test.zig", server / "native_api_test.zig")
    shutil.copy2(here / "reasoning_cache_test.zig", server / "native_api_cache_test.zig")
    shutil.copy2(mod / "chat_template.jinja", server / "native_api_template.jinja")
    with (server / "root.zig").open("a") as root:
        root.write('\ntest { _ = @import("native_api_test.zig"); _ = @import("native_api_cache_test.zig"); }\n')
    build = scratch / "zig/build/cuda.zig"
    needle = '    const server_json = @import("server.zig").addTo(b, server_tests, host, .debug);'
    source = build.read_text()
    if source.count(needle) != 1:
        raise RuntimeError("native server build graph changed; update the test-step insertion")
    step = '\n    b.step("test-native-api-parity", "Native API parity").dependOn(&b.addRunArtifact(b.addTest(.{ .root_module = server_tests, .filters = &.{"native API parity:"} })).step);'
    build.write_text(source.replace(needle, needle + step))
    command = [args.zig, "build", "test-native-api-parity"]
    if args.full:
        command.extend(["test-server-cpu", "server-cpu"])
    if args.cpu:
        command.append(f"-Dcpu={args.cpu}")
    command.extend(["--summary", "all"])
    env = dict(os.environ, TF_GLM_CLEAR_THINKING="0")
    run(command, cwd=scratch, env=env)
    print(f"PASS scratch={scratch}", flush=True)


if __name__ == "__main__":
    main()
