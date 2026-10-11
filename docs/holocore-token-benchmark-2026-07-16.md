# HoloCore token benchmark — 2026-07-16

## Scope

Project under test: `C:\\Cursor projects\\HoloCore`.

Question: how much retrieval context is exposed by no knowledge layer, HoloCore, and Graphify-only for a codebase question?

These are estimated context tokens, not Codex billing counters. Codex's hidden model input/output token usage is not exposed by the local HoloCore or Graphify CLIs.

## Results

| Mode | Measured context | Estimated tokens/query | Notes |
|---|---:|---:|---|
| No knowledge layer / naive corpus | ~100,400 words | ~133,866 | Graphify benchmark's comparable full-corpus estimate |
| Graphify-only | 2,008 nodes / 4,067 edges | ~242 average | Built-in `graphify benchmark` over five sample questions |
| HoloCore corrected unified route | ~780 average returned characters | ~156 average | In-process serialized result payload; Archive/Animus only when relevant |

Relative to the naive baseline, Graphify-only used about 553x fewer estimated tokens. The corrected HoloCore route used about 858x fewer. HoloCore used about 35.5% fewer tokens than Graphify-only on the same five-question set. The end-to-end CLI envelope averaged ~179 tokens because JSON/console formatting adds overhead.

The first measurement was a routing defect: `use_archive=True` for every query, unbounded result lists, and a history classifier that treated generic words such as `error` as prior-work requests. The corrected route makes code/structure queries Atlas-only, caps result counts/snippets, and reserves Animus for explicit prior-work language.

## What Codex did

The project instructions caused this session to:

1. Read `HOLOCORE.md` and the project instructions.
2. Run `holocore status`.
3. Use the unified HoloCore route, which relevance-gates Atlas, Archive, and Animus.
4. Inspect exact local project/tool files afterward.

The `.mcp.json` file wires HoloCore as an MCP server, but that does not mean every Codex turn automatically queries every subsystem. The project policy is relevance-gated and limits each selected subsystem to one route per request.

## Route and retrieval measurements

All HoloCore in-process timings below used one `HoloCoreEngine` instance and a fresh query. Every route begins with the freshness/scope check. `sources` is the returned result-source sequence.

| Query | Route taken | Sources/count | In-process ms | Payload tokens |
|---|---|---:|---:|---:|
| authentication | check → Atlas | Atlas ×3 | 746.67 | 88 |
| main entry point | check → Atlas | Atlas ×3 | 1,082.54 | 121 |
| errors handled | check → Atlas | Atlas ×3 | 255.51 | 125 |
| data layer → API | check → Atlas | Atlas ×3 | 262.22 | 244 |
| core abstractions | check → Atlas | Atlas ×3 | 267.11 | 202 |
| documented architecture decision | check → Atlas → Archive → Animus | one result from each | 358.08 | 232 |
| what we tried last time | check → Atlas → Archive → Animus | one result from each | 362.18 | 231 |

The five code questions averaged 522.81 ms in-process and 156 payload tokens. End-to-end CLI timings for the same five were 1,662.09, 671.68, 617.37, 599.70, and 629.16 ms: average 836.40 ms, minimum 599.70 ms, maximum 1,662.09 ms. The first query paid the largest process/startup cost.

The historical routes are intentionally stricter: one Atlas result, one Archive result, and one Animus result, with 180-character memory excerpts. This prevents prior-work retrieval from becoming a context fan-out.

## Comparator response times

- Graphify `benchmark` CLI: five repeated runs averaged 564.77 ms, minimum 535.33 ms, maximum 605.44 ms.
- Naive raw-corpus read baseline: 1,002 files, ~14.14 million characters, ~3.54 million estimated tokens, 713.69 ms to read locally. This is only the retrieval/read cost; it does not include model processing.
- No layer does not have a meaningful single response time until a model is asked to process the corpus. The raw read is therefore reported separately rather than pretending it is a complete answer latency.

## Reproduction

```powershell
$env:PYTHONIOENCODING='utf-8'
$graph='C:\Brains\Holocore Home\Projects\HoloCore\Atlas\graph.json'
graphify benchmark $graph
holocore status
holocore search 'how does authentication work'
holocore search 'what is the main entry point'
holocore search 'how are errors handled'
holocore search 'what connects the data layer to the api'
holocore search 'what are the core abstractions'
```

## Caveats

- The `~4 tokens per character` conversion is a transparent approximation used by the benchmark tooling, not tokenizer-exact billing.
- The naive and Graphify figures come from Graphify's built-in benchmark and are directly comparable to each other.
- The HoloCore figure counts serialized CLI retrieval output, so it measures the context envelope before any later source-file reading or model answer generation.
- HoloCore and Graphify timings include different process/tool implementations; they are useful operational measurements, not model-latency guarantees.
- The first Graphify run exposed a Windows `cp1252` output issue; setting `PYTHONIOENCODING=utf-8` produced the successful result.
