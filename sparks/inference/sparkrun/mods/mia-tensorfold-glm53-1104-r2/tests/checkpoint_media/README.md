# Complete checkpoint media records

Patch `0820-native-checkpoint-media-capacity.patch` follows 080. The production request policy permits 50 pictures and four clips of 128 sampled frames each. GLM groups two video frames into one tower input, so a valid history can contain 306 prepared media identities. The earlier 64-record development checkpoint format could not preserve such a history.

This patch expands the fixed record array to 306 and the bounded metadata reader to 32 KiB. GLM metadata uses version 2 and rejects the old version. This changes no image/video geometry or serving limit. Server durable storage remains disabled.

The added native test publishes and recovers all 306 identities through real rank files, compares the complete metadata, and restores the actual small FP8 Plan through the recording CUDA adapter. It also rejects an excess count, an image extending past the saved mark and old metadata. The 306 tiny image/group records exercise the accepted count boundary; they do not establish model quality or maximum-resolution GPU capacity.

Apply after the exact nine-patch source and 080, then run from that disposable source directory:

```sh
git apply /absolute/mod/patches/0820-native-checkpoint-media-capacity.patch
/absolute/zig-0.17.0/zig build test-checkpoint-restore-host test-checkpoint-bounded-host compile-glm-native-open -Dcpu=apple_m1 --summary all
```

The [source manifest](source-manifest.json) binds the three changed inputs and outputs. The [receipt](receipt.json) records 103 passing checkpoint executions and 19 successful build steps, including Linux native CUDA startup compilation. No GPU, Spark or production storage operation was performed.
