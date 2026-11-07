# Recorded evidence

These are copied historical responses, not current lab facts. Raw captures and `reference/` are preserved. Generated replay clips points to their source window without shifting timestamps, evaluates no PromQL, and returns successful empty results for missing captures.

| Input | Original prototype source | Window / provenance |
| --- | --- | --- |
| `bounded.json` | `coverage/anchored-home/data/bounded-responses.json` | 2026-11-01T01:27:04.636Z–2026-11-02T01:27:04.636Z, fetched 2026-11-02T02:25:42Z |
| `inference.json` | `coverage/captures/inference-desktop-aurora-queries.json` | 2026-10-31T23:00Z–2026-11-01T00:05Z; Grafana request/result/frame metadata retained |
| `reference/` | Original native-vs-Scenes comparison fixture sources | Home semantic regression inputs; `adapters.js` and `home-model.js` are vendored byte-for-byte into `shared/src/model/semantics.js` and used by the app; the remaining reference samples are not bundled |
| `../runtime/catalog.json` | `strategy-2026-11-02/comparison-2026-11-02/runtime/catalog.json` | Original common/Network replay; source timestamps may include a few points outside the declared daily window, clipped during assembly |

The source paths above are relative to the external research directory `grafana-dashboard-research-2026-11-01/prototype/`, which is not included in this repository. The two raw captures are byte-identical to those originals at integration time. SHA-256: bounded `74fe234bfca773156e64a47ba84ee9cbb74998d437bf2f9c4de1c43e3f2916c9`; inference `9041dc84a0c8e4462a2d0bb39b5c341766785a406842b83ac3da0ff580c1294c`; base catalog `e9931f1e835f2288597778b84462d2fb0cfec3b2a82efcdfbc1d04039cc6a125`.

Domain `queries.json` files declare expressions and capture locators, never samples. Inference keys are `expr:` plus the first 16 hexadecimal characters of SHA-256 of the original executed expression, excluding the `Expr: ` prefix and `Step:` suffix. Executed-expression metadata applies to the whole result, including every anonymous context frame. Assembly records original expression/hash, request expression, source locator, capture window and any alias difference. Changes to selectors or rate intervals are aliases to historical evidence, not newly executed recordings. A captured zero does not establish collector capability or health; interpretation belongs to the domain model.
