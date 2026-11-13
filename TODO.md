# FFmpeg integration

Implement video conversion as a separate milestone. Start with container
remuxing, then add transcoding with one explicit encoding profile.

Both nodes accept an `ArtifactRef` and return a new reference. The source
content remains unchanged. A remux copies encoded streams into another
container; a transcode re-encodes selected streams.

## Implementation order

Complete and verify each step before moving to the next:

1. [x] Add isolated input-path leases and staged output publication to the local artifact store; test isolation, publication, and cleanup.
2. [x] Add configurable FFmpeg/ffprobe discovery, version reporting, and capability checks.
3. [x] Build the asynchronous process runner with bounded diagnostics, timeout handling, and cancellation that terminates and reaps the child before releasing artifact leases.
4. [x] Add typed ffprobe metadata parsing and define explicit stream-selection policies.
5. [x] Implement typed `RemuxVideo` configuration, explicit stream copying, output verification, and artifact publication.
6. [x] Add generated video fixtures and integration coverage for success, incompatible containers, malformed input, missing tools, timeout, and cancellation.
7. [x] Add the import → remux → save playground and installation/testing documentation.
8. [x] Add execution progress events and FFmpeg progress parsing, including unknown durations and throttling.
9. [ ] Implement and test one explicit transcoding profile using the same services.

## 1. Managed artifact file access

- [x] Add managed input-path access for external tools without loading video bytes into Python memory.
- [x] Add temporary output-file creation within the artifact workspace.
- [x] Publish completed output as a new artifact only after successful processing.
- [x] Define extensionless output paths requiring callers to pass an explicit format to external tools.
- [x] Remove partial output when the processing block raises on failure, timeout, or cancellation.
- [x] Define workspace ownership and cleanup while an external process is using files.
- [x] Verify source isolation, successful publication, and failed-output cleanup.

## 2. FFmpeg and ffprobe service

- [x] Locate configurable FFmpeg and ffprobe executables and report missing binaries clearly.
- [x] Record executable versions and check required container/codec capabilities.
- [x] Run tools with `asyncio.create_subprocess_exec()` and argument lists.
- [x] Disable interactive stdin for FFmpeg.
- [x] Drain subprocess output concurrently and retain a bounded stderr tail for diagnostics.
- [x] Return structured errors for launch failures and nonzero exits.
- [x] Return structured errors for invalid probe output.
- [x] Use ffprobe to obtain actual duration, container, and stream metadata.
- [x] Define stream selection for video, multiple audio tracks, subtitles, attachments, and chapters.
- [x] Handle task cancellation and the execution context's cooperative cancellation signal.
- [x] On cancellation or timeout, terminate, allow a bounded grace period, then kill if necessary and reap the process.
- [x] Finish process cleanup before removing temporary files.

## 3. RemuxVideo node

- [x] Define typed configuration for the destination container and stream-selection policy.
- [x] Accept an artifact input and return a new artifact output.
- [x] Build explicit stream mappings and use stream copying (`-c copy`).
- [x] Reject incompatible streams/container combinations with useful diagnostics.
- [x] Avoid silently dropping streams or falling back to transcoding.
- [x] Assign a logical filename matching the new container.
- [x] Verify expected output streams before publishing the artifact.
- [x] Accept `mkv` as an alias for the Matroska output container.
- [x] Return the original artifact when container identity is confirmed and the stream/chapter selection is unchanged.

## 4. Integration tests and playground

- [x] Generate tiny valid video fixtures with known streams; keep the current dummy-file example for filename inference only.
- [x] Test successful remuxing and inspect output with ffprobe.
- [x] Test missing binaries, unsupported formats, malformed input, and process failures.
- [x] Test cancellation and timeout cleanup, including child-process termination.
- [x] Verify the source remains unchanged and unsuccessful runs publish no output artifact.
- [x] Cover multiple audio tracks, subtitles, opaque attachments, cover art, chapters, and idempotency for MKV/MP4/WebM.
- [x] Add a runnable valid-video playground demonstrating import → remux → save.
- [x] Document FFmpeg installation requirements and how to run the integration checks.

## 5. Progress reporting

- [x] Extend execution events with node progress notifications.
- [x] Parse FFmpeg's machine-readable `-progress pipe:1` output.
- [x] Use ffprobe duration to calculate percentage when duration is known.
- [x] Support unknown duration without inventing a percentage.
- [x] Throttle updates and preserve event ordering through completion and cancellation.
- [x] Display conversion progress in the playground.

## 6. TranscodeVideo node

- [ ] Choose one initial encoding profile with explicit container, video codec, audio codec, and quality settings.
- [ ] Define typed profile configuration and validate supported settings.
- [ ] Specify handling of subtitles, extra audio tracks, attachments, and chapters.
- [ ] Reuse artifact staging, process execution, cancellation, and progress services.
- [ ] Verify output codecs, streams, and duration using valid fixtures.
- [ ] Document quality/speed tradeoffs and supported profile limitations.

## References

- [FFmpeg command-line documentation](https://ffmpeg.org/ffmpeg.html)
- [ffprobe documentation](https://ffmpeg.org/ffprobe.html)
- [Python asyncio subprocess documentation](https://docs.python.org/3/library/asyncio-subprocess.html)

# Generic node library

Add reusable nodes for the areas below. Extend existing nodes where their
contracts already cover the operation. Define typed ports and configuration,
register each node, and document examples as each group is implemented.

## Shared contracts

- [ ] Define reusable structured-value and list port types and their validation rules.
- [ ] Define handling of missing values, explicit nulls, empty values, and incompatible types without implicit coercion.
- [ ] Define how configurable object fields and variable-length inputs appear in node specs and graph validation.
- [ ] Keep transformations from mutating their input values.
- [ ] Test normal behavior, boundary cases, and useful validation/execution errors for each group.

## Structured data

- [ ] `GetField`: retrieve a field with an explicit missing-field policy; distinguish missing fields from null values.
- [ ] Define nested field paths using explicit key/index segments, including keys containing dots.
- [ ] `SelectFields`: select and optionally rename object fields; define missing-field and name-collision behavior.
- [ ] `BuildObject`: construct an object from named inputs.
- [ ] `MergeObjects`: merge objects with an explicit duplicate-key policy; start with shallow merging.

## Lists

- [ ] `MakeList`: assemble an ordered list from inputs.
- [ ] `GetItem`: retrieve an item by index; define negative-index and out-of-range behavior.
- [ ] `Flatten`: flatten nested lists with an explicit depth.
- [ ] `Unique`: remove duplicates while preserving first-occurrence order; define equality for supported values.
- [ ] `Sort`: sort values or use an object field as a key; define direction, stability, missing keys, and mixed-type handling.
- [ ] `Count`: return the number of items, including zero for an empty list.

## Strings

- [ ] `Join`: join a list of strings with a separator.
- [ ] `Split`: split on a literal separator with an optional split limit; define empty-part behavior.
- [ ] `Replace`: perform literal replacement with an optional replacement limit.
- [ ] `RegexExtract`: extract matches and capture groups with explicit first-match/all-matches and no-match behavior.
- [ ] `RegexReplace`: perform regex substitution separately from literal replacement; validate patterns and replacement syntax.
- [ ] `Trim`: remove leading/trailing whitespace or an explicit set of characters.
- [ ] `ChangeCase`: provide explicit lower, upper, and casefold operations.

## Paths

Path nodes perform lexical operations without reading or changing the filesystem.

- [ ] Define native versus explicitly selected POSIX/Windows path semantics.
- [ ] `JoinPath`: combine path components; specify how absolute later components are handled.
- [ ] `Basename`: return the final path component.
- [ ] `Stem`: return the final component without its last extension.
- [ ] `Extension`: return the last extension; define leading-dot, extensionless, and multi-extension behavior.
- [ ] `ParentPath`: return the parent path with defined root and relative-path behavior.
- [ ] `RelativePath`: compute a path relative to a base; define outside-base and incompatible-root behavior.

## Logic

- [ ] `Compare`: support equality and ordering operators with explicit compatible-type rules.
- [ ] `All`: combine a list of booleans; define the empty-list result.
- [ ] `Any`: combine a list of booleans; define the empty-list result.
- [ ] `Not`: negate a boolean without truthiness coercion.
- [ ] `Coalesce`: choose the first present, non-null value; preserve zero, false, and empty strings unless explicitly configured otherwise.
- [ ] Document that these nodes produce values; conditional branch scheduling is a separate engine capability.

## HTTP requests

- [ ] Define an `HTTPRequest` node with typed method, URL, query parameters, headers, and optional body inputs; validate supported URL schemes and conflicting body formats.
- [ ] Support explicit JSON, text, and artifact request bodies with appropriate content types.
- [ ] Return status, headers, final URL, and a response body with explicit JSON, text, or artifact handling; define decoding and invalid-JSON errors.
- [ ] Define successful-status policy and structured errors for rejected statuses, connection failures, TLS failures, and timeouts; retain bounded response diagnostics.
- [ ] Add connect/read/overall timeouts, response-size limits, and cancellation that closes connections and cleans up staged artifacts.
- [ ] Define redirect limits, method/body behavior across redirects, and handling of credentials when the destination changes.
- [ ] Support credential references without embedding secrets in workflow exports, logs, or run diagnostics.
- [ ] Define explicit retry behavior for transient failures, including backoff and `Retry-After`; account for method idempotency and replayable bodies.
- [ ] Stream artifact uploads/downloads through managed leases and staging; publish downloads only after successful completion.
- [ ] Test against a local HTTP server for request encoding, response modes, status errors, redirects, limits, timeout, cancellation, and cleanup.
- [ ] Document configuration and add an example workflow consuming a JSON endpoint.

## Conditional routing

Conditional routing requires scheduler support as well as node definitions.

- [ ] Define an `If` routing node with a boolean condition and typed payload routed to exactly one of its true/false outputs.
- [ ] Represent an inactive output separately from a missing value or explicit null.
- [ ] Define when downstream nodes become skipped and propagate branch inactivity without executing their side effects.
- [ ] Define joins with active and inactive inputs, including nodes with required inputs from mutually exclusive branches.
- [ ] Add a branch merge node that forwards the selected branch's value; define zero-active and multiple-active input behavior.
- [ ] Validate routed payload compatibility and branch/merge connections before execution.
- [ ] Expose skipped-node reasons and routing decisions in run records and events, with deterministic event ordering.
- [ ] Define workflow output behavior when its source branch is inactive.
- [ ] Preserve failure propagation and cancellation semantics across active and skipped branches.
- [ ] Test both condition outcomes, nested branches, fan-out, joins, null payloads, skipped side effects, failures, and cancellation.
- [ ] Add an example workflow that compares metadata, selects a processing branch, and merges its result before saving.

## Scripting: arbitrary Python

- [ ] Define a `RunPython` node accepting arbitrary Python source as configuration, with a documented entry point and input/output mapping contract.
- [ ] Declare input/output port types in configuration so graph validation can check connections before execution.
- [ ] Run scripts in a separate Python process; define interpreter selection, dependency availability, working directory, and environment inheritance.
- [ ] Define a serialized input/output protocol and validate returned values against declared output ports; keep script logs separate from protocol output.
- [ ] Define artifact access through managed input leases and staged output publication, including cleanup on failure.
- [ ] Add configurable timeouts, bounded logs/results, and cancellation that terminates and reaps the script process before releasing resources; define child-process handling.
- [ ] Return useful structured errors for syntax errors, exceptions, nonzero exits, invalid results, and missing dependencies.
- [ ] Document that scripts execute trusted code with the interpreter user's permissions; a separate process is not a security sandbox.
- [ ] Test successful execution, typed input/output validation, log capture, failures, timeout, cancellation, and artifact cleanup.
- [ ] Add an example workflow using a Python script to transform structured metadata.

# Backend API for frontend GUI clients

The Tauri + React + React Flow desktop client is planned separately in
[TODO-desktop.md](TODO-desktop.md).

Expose the library through a documented API so desktop and web GUIs can
discover node capabilities, author workflows, and manage execution. Keep the
core library callable directly from Python and share validation and execution
behavior between API and library callers.

- [ ] Define versioned request/response contracts, stable identifiers, and structured errors suitable for displaying field, node, port, and edge diagnostics in a GUI.
- [ ] Expose node discovery with typed ports, configuration schemas, defaults, descriptions, and capabilities needed to build editors dynamically.
- [ ] Add workflow draft create/read/update/delete/list endpoints using the existing persistence layer; support incomplete drafts and define concurrent-edit conflict handling.
- [ ] Support workflow import/export and round-tripping editor metadata such as node positions without affecting execution semantics.
- [ ] Expose workflow validation independently of saving or running, returning the library's structured diagnostics.
- [ ] Submit runs with workflow inputs and an immutable workflow snapshot; return a run ID promptly and execute outside the request lifecycle.
- [ ] Expose run status, node states, outputs, errors, and cancellation; define repeated cancellation and terminal-run behavior.
- [ ] Persist run snapshots and define recovery/status behavior for runs interrupted by a backend restart.
- [ ] Stream run state and progress events through SSE or WebSockets; define ordering, reconnect/resume behavior, and how clients reconcile events with run snapshots.
- [ ] Provide managed artifact upload, metadata, and download endpoints with streaming, size limits, and explicit ownership/retention rules.
- [ ] Define how GUI clients supply file inputs for local versus remote backends, including when paths are server-local and when uploads are required.
- [ ] Define deployment/access settings for local desktop and hosted use, including bind address, authentication, allowed origins, and access to filesystem or scripting capabilities.
- [ ] Define run admission limits, service shutdown behavior, and cleanup of active work and temporary artifacts.
- [ ] Publish an API schema and examples covering node discovery, draft editing, validation, run submission, progress, cancellation, and result retrieval.
- [ ] Add API integration tests verifying parity with direct library calls, error contracts, persistence, event reconnection, cancellation, and artifact transfer.
- [ ] Add a runnable backend entry point and a minimal client example demonstrating the complete GUI-facing workflow lifecycle.
