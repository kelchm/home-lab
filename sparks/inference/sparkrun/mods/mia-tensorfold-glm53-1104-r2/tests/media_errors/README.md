# Deferred media request errors

`0760-native-media-request-errors.patch` preserves request-error classification when known media input failures occur during prefill. GLM explicitly classifies preparation-budget expiry and known video decoding failures. Unknown CUDA, memory, protocol and other errors retain server-failure behavior. Other families keep their existing classification unless they supply the optional classifier.

The native engine carries an internal `refused` finish reason through its normal mailbox. The shared chat layer maps that to a request refusal. OpenAI and Messages retain `invalid_request_error`; streamed Responses retains its existing `invalid_prompt` code. A stream that already sent headers reports the classified error in its established error event. Genuine server failures still produce HTTP 500 before headers or the existing server-error event afterward. Refused replies do not enter Recall's learned-reply store.

The [source manifest](source-manifest.json) records the patch, changed native files, baseline marker and exact test inputs. The [receipt](receipt.json) records the focused gate and an external negative control. Tests use the actual lane host with whole, stepped and warm prefill; one refused request does not interrupt another stream. The real HTTP route/template/translation fixture covers all three APIs, whole and streaming, with request and server errors (12 cells). Linux CUDA startup compiles. Mapping the refusal back through the old server-error path fails the intended HTTP 500 assertion without a compilation failure.

Run against the documented immutable 13-patch source:

```sh
python3 tests/media_errors/run.py --source /tmp/glm1104-runtime13-locked-source --zig /absolute/zig-0.17.0/zig
```

This patch is numbered after 0750, the separate single-pass video correction. Its changed files do not overlap that video implementation. The final composed gate remains mandatory. No model, GPU or Spark operation was performed.
