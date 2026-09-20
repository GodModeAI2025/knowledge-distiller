# Local Runner and API

The runner is a small, production-shaped layer around Knowledge Distiller's deterministic
tools. It validates an existing `*.knowledge.json` file or derives local artifacts from it.
It does **not** extract arbitrary documents, invoke a model, execute a command, send
telemetry, or make an outbound network request.

## What it produces

Every request is content-addressed from the input identity and SHA-256, operation,
parameters, tool hashes, specification hash, schema hash, Python version, and relevant
optional dependency version. Its files live only below the explicitly selected output root:

```text
<output-root>/
├── .locks/
└── runs/
    └── <request-key>-a0001/
        ├── request.json                 # write-once request envelope
        ├── source.knowledge.json        # immutable input snapshot
        ├── validation.json              # deterministic validation result
        ├── stages/                      # write-once stage receipts
        ├── artifacts/
        │   ├── graph.knowledge.json
        │   ├── graph.knowledge.md       # optional
        │   ├── graph.knowledge.html     # optional, self-contained viewer
        │   ├── graph.knowledge.cypher   # optional, Neo4j 5 statements
        │   ├── graph.knowledge.ctxt     # optional, KD-CTXT/1
        │   ├── graph.knowledge.canvas   # optional, Obsidian/JSON Canvas
        │   └── bundle/                  # optional
        └── manifest.json                # write-once final record
```

The final manifest records:

- input path relative to the input sandbox, size, and SHA-256;
- operation and normalized parameters;
- runner/Python/dependency versions plus hashes of the runner, tools, spec, schema,
  export renderer, viewer renderer, and all three viewer assets;
- stage status, start/end time, duration, diagnostic summary, and output hashes;
- Distiller and format-spec versions declared by the graph;
- validation result and any sanitized failure. `conformance_score` measures contract
  conformance and `semantic_accuracy_evaluated` remains `false`; artifact rendering does
  not replace either with an unsupported semantic-quality claim;
- every output's relative path, size, type, SHA-256, and directory file count; and
- explicit `network_access: false` and `telemetry: false` declarations.

A manifest and a stage receipt are never overwritten. An identical successful request is
reused only when its request fields, exact stage sequence, complete expected output set, record
shape, receipt copies, and every output hash all verify. An empty, partial, extended, reordered,
or otherwise forged record set is not reusable. If an output is missing or changed,
the runner creates a new attempt (`a0002`, `a0003`, …) and preserves the old manifest. If a
process stops before writing its final manifest, a later request may resume the incomplete
attempt only when its request and the contiguous prefix of exact stage receipts and hashes still
match.

## Command line

Both roots are explicit. Input paths may be absolute for the CLI only when they are lexically under
the declared or canonical `--input-root`. The runner opens the root, each directory, and the final
regular file descriptor-relatively with symlink following disabled. Fingerprinting, strict JSON
parsing, and the immutable snapshot all use the bytes read from that one verified descriptor;
pathname replacement after open cannot redirect the run.

The canonical input-root path is itself opened by a component-by-component descriptor walk from
the filesystem root. Output-root creation and generated-file writes use the same no-follow,
descriptor-relative discipline. Existing `runs`, `.locks`, attempt, parent, destination, receipt,
and artifact symlinks or non-regular collisions fail closed instead of redirecting a write. These
guarantees require POSIX `dir_fd`/`O_NOFOLLOW`-style filesystem primitives; the runner rejects the
operation on platforms without them rather than silently weakening the sandbox. Native Windows is
therefore not currently a supported runner platform.

```bash
python3 scripts/run_pipeline.py validate example.knowledge.json \
  --input-root /absolute/read-root \
  --output-root /absolute/write-root
```

```bash
python3 scripts/run_pipeline.py build example.knowledge.json \
  --input-root /absolute/read-root \
  --output-root /absolute/write-root \
  --artifacts json md bundle html cypher ctxt canvas
```

`run` is an alias for `build`. Supported artifacts are deliberately closed to `json`, `md`,
`bundle`, `html`, `cypher`, `ctxt`, and `canvas`; canonical JSON is always generated. HTML is
rendered by the repository's pure in-process viewer function. Cypher, CTXT, and Canvas use
the pure functions documented in [EXPORTS.md](EXPORTS.md). No renderer invokes a subprocess
or network service. `--no-resume` starts a new safe attempt instead of reusing verified
receipts from an interrupted one. Exit status is `0` for a successful run, `1` for a
recorded pipeline/validation failure, and `2` for a rejected CLI request.

Graph JSON is parsed strictly: duplicate object names, `NaN`, `Infinity`, invalid UTF-8, invalid
syntax, excessive nesting, and isolated Unicode surrogates are rejected. The strict parser itself
is included in the toolchain hashes, so parser changes produce a different content-addressed
request key.

Artifact names, filenames, and manifest kinds are fixed:

| Request name | Output | Manifest kind |
|---|---|---|
| `json` | `artifacts/graph.knowledge.json` | `knowledge_json` |
| `md` | `artifacts/graph.knowledge.md` | `knowledge_markdown` |
| `bundle` | `artifacts/bundle/` | `knowledge_bundle` |
| `html` | `artifacts/graph.knowledge.html` | `knowledge_html` |
| `cypher` | `artifacts/graph.knowledge.cypher` | `knowledge_cypher` |
| `ctxt` | `artifacts/graph.knowledge.ctxt` | `knowledge_ctxt` |
| `canvas` | `artifacts/graph.knowledge.canvas` | `knowledge_canvas` |

Every selected file or directory is hashed in its stage receipt and final manifest. A later
request with the same artifact set is reusable only when all recorded hashes still match.

Two independent byte budgets bound generated state. `--max-run-bytes` defaults to 512 MiB for
one attempt directory, and `--max-output-root-bytes` defaults to 4 GiB across the complete output
root. Both values are part of the normalized request identity. Before a new attempt directory is
created, the runner verifies that the existing root is within budget and that the immutable
request envelope fits both limits. It then rechecks under one output-root-wide lock for every
atomic file write, write-once receipt/manifest, and bundle installation; transient replacement
bytes count too. A limit breach fails closed with no over-budget write. Lock files use a fixed
256-stripe set plus one quota lock, so varying request keys cannot create an unbounded lock-file
set.

Quota traversal is descriptor-anchored and never follows links. The limits count logical file
payload bytes, not allocated filesystem blocks or directory metadata. Bundle generation uses a
private operating-system temporary directory and installs the completed, measured tree only while
holding the quota lock; operators who need a hard limit for temporary-filesystem consumption must
also configure that filesystem separately. The output root remains an exclusive runner boundary:
uncooperative external writers can cause a denial of service and do not participate in its lock.

## Local HTTP API

The authenticated server binds to `127.0.0.1` only. Its readable input root and writable output
root are fixed at startup; requests cannot replace either value. Every endpoint, including health
and MCP discovery, requires the per-process bearer token. Supply an explicit token of at least 32
visible ASCII characters for automation, or omit it and the server generates a cryptographically
random token and writes it once to the local startup terminal. Tokens are never included in HTTP or
MCP responses.

`--token` is convenient for automation but, like any command-line secret, can be retained in shell
history or exposed to same-user process inspection on some operating systems. Prefer the generated
startup token for an interactive local session. For supervised startup, `--token-file` avoids
putting the secret in argv. The file must be a current-user-owned regular file with exactly `0400`
or `0600` POSIX mode, exactly one hard link, no final symlink, and at most 1 KiB. One final newline
is removed; the remaining token must still be 32–512 visible ASCII characters. If an external
supervisor supplies `--token`, its configuration, process visibility, terminal capture, and logs
remain part of that supervisor's secret-handling boundary.

```bash
python3 scripts/serve_api.py \
  --input-root /absolute/read-root \
  --output-root /absolute/write-root \
  --token-file /protected/knowledge-distiller.token \
  --port 8765
```

Endpoints:

| Method | Path | Authentication and JSON body |
|---|---|---|
| `GET` | `/health` | bearer token; no body |
| `POST` | `/v1/validate` | bearer token; `{"input":"relative.knowledge.json"}` |
| `POST` | `/v1/build` | bearer token; `{"input":"relative.knowledge.json","artifacts":["html","cypher","ctxt","canvas"]}` |
| `POST` | `/v1/run` | bearer token; alias of `/v1/build` |
| `POST` | `/mcp` | bearer token; JSON-RPC 2.0 request |

Example:

```bash
curl -sS http://127.0.0.1:8765/v1/build \
  -H 'Authorization: Bearer replace-with-a-long-random-local-token' \
  -H 'Content-Type: application/json' \
  --data '{"input":"example.knowledge.json","artifacts":["html","cypher","ctxt","canvas"]}'
```

API input paths must be relative. The server requires the exact bound `Host` authority
(`127.0.0.1:<actual-port>`), a loopback peer, and a valid bearer token. If an `Origin` header is
present it must exactly equal that local HTTP origin; foreign and `null` origins are rejected.
There are no `Access-Control-Allow-*` headers and `OPTIONS` does not enable CORS. The server also
rejects absolute targets and input paths, `..`, every symlink in an input path, unknown fields,
unsupported artifacts, missing or non-JSON content types, chunked bodies, oversized bodies,
oversized inputs, and query strings. HTTP/MCP JSON uses the same strict parser as graph input.
Responses contain only bounded diagnostics and output-root-relative paths; configured absolute
roots and the bearer token are redacted defensively. There is no endpoint for shell commands,
arbitrary Python, URLs, or client-selected output paths.

The default limits are:

- request body: 64 KiB;
- response body: 1 MiB;
- graph input: 64 MiB, nested at most 256 containers deep;
- one run attempt: 512 MiB;
- complete output root: 4 GiB;
- concurrent HTTP requests: 16; and
- concurrent validation/build runs: 2.

They can be changed at server startup with `--max-body-bytes`, `--max-response-bytes`,
`--max-input-bytes`, `--max-run-bytes`, `--max-output-root-bytes`,
`--max-concurrent-requests`, and `--max-concurrent-runs`. The run limit may not exceed the request
limit. Capacity is rejected with `503` rather than creating unbounded worker or pipeline
concurrency; exhausted output byte budgets are rejected with `507`. Increasing any limit should
be a deliberate local operational decision.

At process import the runner captures hashes for its loaded runner, validators, renderers, schema,
specification, strict parser, and viewer assets. API startup pins that process fingerprint. Every
operation checks the on-disk files against it before entering the pipeline and again before a
successful manifest is finalized. The first mismatch latches the process into a failed-safe state:
all later runs return `503 toolchain_changed` until the API process is restarted, even if the file
is subsequently restored. `/health` remains a liveness endpoint and does not clear or claim this
run-readiness state.

`GET /health` is intentionally authenticated. It is a narrow process-liveness response (service
and runner versions plus the fixed no-network/no-telemetry declarations), not a readiness probe:
it does not touch input files, validate output-root writability, disclose roots, expose the token,
or assert that a future graph run will succeed. Requiring the token also prevents unauthenticated
browser or local-process probing of the service.

## Minimal MCP-compatible endpoint

`POST /mcp` implements the JSON-RPC methods needed for a narrow local tools surface:

- `initialize`
- `ping`
- `notifications/initialized`
- `tools/list`
- `tools/call`

The only advertised tools are:

- `knowledge_validate`
- `knowledge_build`

They call exactly the same guarded functions as `/v1/validate` and `/v1/build`. Tool schemas
set `additionalProperties: false` and enumerate the same seven artifact names. An argument
such as `command`, `url`, `output_root`, or an unknown artifact is returned as a tool error
rather than executed. Adding the formats does not add another MCP tool name or broaden the
operation beyond local graph validation/building.

Example tool listing:

```bash
curl -sS http://127.0.0.1:8765/mcp \
  -H 'Authorization: Bearer replace-with-a-long-random-local-token' \
  -H 'Content-Type: application/json' \
  --data '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
```

This is deliberately a minimal Streamable-HTTP-style JSON-RPC endpoint, not a general MCP
host: it has no resources, prompts, sampling, elicitation, subscriptions, or remote transport.

## Operational boundaries

- Treat the input root as read-only source material and the output root as generated state.
- Give the runner exclusive control of its output root. Symlinks and special files are rejected,
  but a different process with permission to replace ordinary directories can still deny service;
  this tool is not a substitute for operating-system account or directory isolation.
- Authentication is defense in depth for a loopback-only service, not permission to expose it
  through a proxy or bind it to a public interface.
- The runner snapshots a graph; it never edits the caller's input file.
- Renderers receive isolated in-memory copies of the derived graph. They do not overwrite
  its provenance, `conformance_score`, or any semantic-evaluation data.
- A validation failure is evidence in an immutable failed manifest, not permission to repair
  the graph automatically.
- The built-in HTML renderer escapes graph data before embedding it in the self-contained
  viewer. Generated Markdown, bundle, CTXT, Canvas, and Cypher files still contain encoded or
  displayed source-derived text; consumers must treat it as data rather than instructions.
- Cypher/CTXT/Canvas round-trip and database limitations are documented in
  [EXPORTS.md](EXPORTS.md). In particular, the runner does not geocode, enrich, redact, infer
  source authority, or generate reasoning traces.
