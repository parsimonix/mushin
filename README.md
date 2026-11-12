# mushin
Arbitrary workflow composer / validator.

See [the design document](DESIGN.md) for the proposed backend architecture and implementation milestones.

## Media tool discovery

Install FFmpeg and ffprobe separately; they are not Python dependencies.
Check the installed build at startup:

```python
from mushin.media import MediaTools, ToolConfig

tools = MediaTools.discover()  # Searches PATH for ffmpeg and ffprobe.
print(tools.ffmpeg.version)
print(tools.ffprobe.version)
tools.require(muxers=["matroska"], demuxers=["mov"])

# Override either executable with a name or path, including paths with spaces.
tools = MediaTools.discover(ToolConfig(
    ffmpeg="/opt/ffmpeg/bin/ffmpeg",
    ffprobe="/opt/ffmpeg/bin/ffprobe",
    timeout=10,
))
```

Discovery is synchronous and has a timeout per command. Use it before starting
async workflow execution. `MediaToolError` provides `code` and `executable`
attributes for missing tools, launch errors, timeouts, invalid output, and
missing capabilities. Requirements use exact FFmpeg names; encoder names
(such as `libx264`) differ from codec names (such as `h264`). Capability lists
describe the installed build, not whether a particular stream/container
combination or hardware encoder will work. Remuxing needs no encoder.

Run discovery tests without installing FFmpeg:

```bash
uv run python -m unittest discover -s tests -p test_media_tools.py
```

## Async media processes

Use `ProcessRunner` from async workflow code with the tools discovered at startup:

```python
from mushin.media import ProcessRunner

runner = ProcessRunner()

async def check_version(tools, context):
    result = await runner.ffmpeg(
        tools, ["-version"], timeout=10, cancellation=context.cancellation,
    )
    return result.stdout.decode("utf-8")
```

The runner passes argument lists directly and disables stdin. FFmpeg also gets
`-nostdin`. Stdout defaults to a 4 MiB limit; exceeding it stops the child with
`MediaToolError.code == "output_limit"`. Stderr retains its final 16 KiB, decoded
with replacement for invalid UTF-8. Both limits are configurable.

Timeouts raise `MediaToolError` with code `process_timeout`; nonzero exits use
`command_failed`. Errors include `returncode` and the bounded `stderr` tail.
Task cancellation or a set cancellation event raises `asyncio.CancelledError`.
Before returning or raising, the runner terminates the child, waits up to its
configurable grace period (one second by default), then kills and reaps it if
needed. Cleanup can extend beyond the processing timeout.

Await the runner **inside** artifact input/output contexts so process cleanup
finishes before temporary files are removed. The runner owns a direct child;
it does not manage descendant processes inheriting its output pipes.

## Content metadata and stream selection

`MediaProbe` reads actual media properties using ffprobe's JSON output. It
keeps those properties separate from filename-inferred `VideoMetadata`:

```python
from mushin.media import MediaProbe, StreamSelectionPolicy

async def inspect_artifact(tools, store, ref, context):
    info = await MediaProbe(tools).probe_artifact(
        store, ref, timeout=30, cancellation=context.cancellation,
    )
    selection = StreamSelectionPolicy().select(info)
    return info, selection
```

`MediaInfo` contains container names, duration in seconds, typed streams,
chapters, tags, and stream dispositions. Missing or `N/A` duration remains
`None`; it is not inferred from filenames or individual streams. Additional
ffprobe fields are ignored. Invalid JSON or invalid required metadata raises
`MediaToolError` with code `invalid_probe_output`. Process errors retain their
original codes. `probe_path()` also accepts a caller-owned local file, useful
for checking staged output before publishing it.

The default stream policy preserves all video, audio, subtitle, attachment,
cover-art, and data streams, plus chapters. Each stream category accepts
`all`, `first`, or `none`; `first` uses probe order, regardless of language or
default disposition. Cover art (`pictures`) is selected separately from
ordinary `video` and `attachments`. For example:

```python
policy = StreamSelectionPolicy(
    audio="first", subtitles="none", attachments="none",
    pictures="none", data="none", chapters="drop",
)
```

Unknown stream types raise `unsupported_stream_type` unless `unknown="drop"`
is explicit. Selecting no streams raises `empty_stream_selection`. The result
contains absolute stream indices in input order and generates explicit
`-map 0:<index>` and `-map_chapters` arguments for a single input. Destination
container compatibility and conversion remain separate checks; selection
does not silently discard streams to fit a container.

Probe and selection tests use simulated ffprobe responses:

```bash
uv run python -m unittest discover -s tests -p test_media_probe.py
```

## Workflow storage

Handwritten workflows can use named node keys without IDs. See
[the playground workflow](workflows/playground.yaml) for a complete example.

```yaml
name: My draft
nodes:
  guess:
    type: video.guess
inputs:
  - name: file
    target_node: guess
    target_port: file
```

Import a file with any filename from async Python code:

```python
from mushin.persistence import load_workflow_file

workflow = await load_workflow_file("workflows/playground.yaml")
```

Node keys resolve to generated UUIDs and serve as default display names.
Each import generates fresh IDs unless explicitly supplied. Save the imported
workflow in the repository to retain its identity. Unknown named references
are rejected during import; other graph validation remains separate.

Use the local YAML repository from async Python code:

```python
from mushin.models.node import Workflow
from mushin.persistence import YamlWorkflowRepository

repository = YamlWorkflowRepository("./workflows")
workflow = Workflow(name="My draft")
await repository.save(workflow)
restored = await repository.load(workflow.id)
drafts = await repository.list()
await repository.delete(workflow.id)
```

Documents use `<workflow UUID hex>.yaml` filenames and safe YAML loading.
Incomplete graphs can be saved as drafts; validate them before execution.
Saving replaces the document atomically and does not preserve YAML comments
or formatting. Missing documents raise `FileNotFoundError`; invalid stored
documents raise `WorkflowDocumentError`. Listing fails on invalid workflow
documents and ignores files outside the repository's UUID naming convention.
