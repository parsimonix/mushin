# Desktop application: Tauri + React + React Flow

Build a desktop workflow creator using Tauri for the application shell,
React and TypeScript for the UI, and React Flow for the workflow canvas.
Run the Python backend as a bundled sidecar process using the API planned
in [TODO.md](TODO.md#backend-api-for-frontend-gui-clients).

This route uses a native application window with webview-rendered content.
Keep workflow validation and execution in mushin; React Flow represents and
edits the graph rather than executing it.

## Architecture and implementation order

```text
Tauri desktop shell
  ├─ React + TypeScript + React Flow editor
  │    └─ HTTP requests + streamed run events
  └─ Managed Python backend sidecar
       └─ mushin API → library, persistence, artifacts, execution
```

Complete a small vertical slice before expanding the editor:

1. [ ] Prototype node discovery → place/connect/configure nodes → validate → run → display results.
2. [ ] Add workflow persistence, import/export, undo/redo, and editor metadata.
3. [ ] Add run monitoring, cancellation, reconnect handling, and artifact access.
4. [ ] Package the Python sidecar and verify an installed application on each supported platform.
5. [ ] Measure performance, improve accessibility, and prepare release builds.

## Desktop shell and development setup

- [ ] Choose initial target operating systems and architectures; define a supported build/test matrix.
- [ ] Scaffold Tauri with React, TypeScript, and a frontend build tool such as Vite in a dedicated desktop directory.
- [ ] Add React Flow and establish the application layout: node palette, canvas, configuration inspector, and run/results panel.
- [ ] Keep Rust responsibilities focused on desktop integration and backend lifecycle management.
- [ ] Provide a documented development command that starts the frontend and backend with useful logs and predictable shutdown.
- [ ] Establish application identity, window behavior, menus, keyboard shortcuts, and native file dialogs.

## Python backend lifecycle and API connection

- [ ] Add a backend entry point suitable for both development and a packaged executable.
- [ ] Have Tauri start the backend, wait for readiness, and surface startup failures with bounded diagnostics.
- [ ] Bind the local API to loopback on an allocated port; pass connection details through a defined startup handshake.
- [ ] Use a per-launch authentication token and restrict allowed origins; keep tokens out of logs and persisted workflows.
- [ ] Define Tauri capabilities and content security policy for the required API and desktop operations.
- [ ] Add a typed API client, preferably generated from the backend schema, and check API compatibility during startup.
- [ ] Define backend crash recovery and user-visible disconnected/reconnecting states.
- [ ] Shut down the backend and active subprocesses on application exit; define confirmation or cancellation behavior for active runs.
- [ ] Keep local paths, backend paths, and uploaded artifacts distinct so a future remote backend remains possible.

## Workflow canvas

- [ ] Map workflow/node/edge identifiers to React Flow identifiers without changing backend identities.
- [ ] Generate the node palette from backend discovery metadata, with search and categories.
- [ ] Render custom nodes with named typed input/output handles and clear configuration/status indicators.
- [ ] Support placing, moving, selecting, duplicating, and deleting nodes, plus creating and deleting connections.
- [ ] Provide connection feedback using discovered port types; retain backend validation as the authoritative check.
- [ ] Support pan, zoom, fit-to-view, multi-selection, and keyboard navigation.
- [ ] Expose workflow-level input/output bindings in the editor.
- [ ] Define editing behavior for configurable ports, unknown node types, and incomplete drafts without silently discarding data.
- [ ] Add optional automatic layout while retaining user-controlled positions.

## Configuration and document editing

- [ ] Build configuration forms from schemas, including defaults, optional values, enums, nested objects, lists, and field diagnostics.
- [ ] Provide suitable editors for templates, expressions, and Python source as their node types become available.
- [ ] Support credential references without persisting secret values in workflow documents.
- [ ] Separate backend workflow data, persisted editor metadata, and transient UI state.
- [ ] Persist node positions and other necessary editor metadata with workflow drafts without affecting execution semantics.
- [ ] Implement new/open/save/save-as and workflow import/export using the backend contracts.
- [ ] Track unsaved changes and define close/reload behavior and concurrent-save conflict handling.
- [ ] Add undo/redo for graph and configuration edits, treating a drag or grouped edit as one action.
- [ ] Display validation diagnostics on the affected nodes, ports, edges, and fields with navigation to each issue.

## Execution, events, and results

- [ ] Collect and validate workflow inputs before submission, including file selection and artifact upload where required.
- [ ] Submit an immutable workflow snapshot and associate displayed results with that snapshot when the editor changes.
- [ ] Display queued/running/completed/failed/cancelled/skipped states as supported by the backend.
- [ ] Show per-node progress, including unknown totals, and keep progress updates separate from terminal success.
- [ ] Subscribe to ordered run events and reconnect using the backend's resume/snapshot reconciliation contract.
- [ ] Provide cancellation with visible acknowledgement and final status.
- [ ] Display structured errors, bounded logs, output values, and artifact metadata/download actions.
- [ ] Add run history and inspection of persisted run snapshots.
- [ ] Render conditional routing decisions and skipped branches when backend routing support is available.

## Performance and accessibility

- [ ] Choose representative small and large workflows and measure startup time, memory, canvas interaction, and event-update responsiveness.
- [ ] Keep dragging, selection, zoom, and transient edits local; avoid backend requests for every pointer movement.
- [ ] Limit React subscriptions and rerenders to affected nodes; batch progress updates and bound log/history rendering.
- [ ] Profile large graphs before selecting rendering or virtualization optimizations.
- [ ] Verify keyboard operation, focus handling, readable contrast, and status indicators that do not rely on color alone.
- [ ] Test display scaling, window resizing, and platform-specific webview behavior.

## Packaging and release

- [ ] Choose and validate Python executable packaging, including dynamic imports, runtime dependencies, and bundled resources.
- [ ] Build the Python sidecar for each supported OS/architecture and include it in the corresponding Tauri bundle.
- [ ] Decide whether FFmpeg/ffprobe are bundled or discovered separately; expose missing-tool diagnostics in the GUI.
- [ ] Store drafts, artifacts, logs, and settings in appropriate user-data locations rather than the installation directory.
- [ ] Verify first launch and a complete workflow run on clean machines without a development Python or Node installation.
- [ ] Define signing, installer formats, upgrades, data migrations, and uninstall/data-retention behavior.
- [ ] Document third-party notices and licenses for shipped dependencies.

## Verification and examples

- [ ] Test workflow/React Flow conversion and round-tripping, including editor metadata and unknown node types.
- [ ] Test graph editing, configuration diagnostics, undo/redo, and unsaved-change behavior.
- [ ] Add end-to-end coverage for create → save → reopen → validate → run → inspect output.
- [ ] Exercise backend startup failure, crash, event reconnect, cancellation, and application shutdown during a run.
- [ ] Include a small pure-data sample workflow and an optional media workflow using the existing playground capabilities.
- [ ] Document development, packaging, and the boundary between frontend state and backend responsibilities.

## References

- [Tauri architecture](https://v2.tauri.app/concept/architecture/)
- [Tauri sidecar executables](https://v2.tauri.app/develop/sidecar/)
- [React Flow documentation](https://reactflow.dev/learn)
