# mushin
Arbitrary workflow composer / validator.

See [the design document](DESIGN.md) for the proposed backend architecture and implementation milestones.

## Run the video sample

With FFmpeg and ffprobe on PATH, run:

```bash
uv run python -m mushin.remux_playground
```

This generates a two-second MP4 with a moving test pattern and an audio tone,
then runs **import → remux to MKV → remux MKV again → save** through the
workflow runner. It prints node events, probes the saved MKV, checks that
codecs and duration are preserved, and verifies that the source is unchanged.
The second remux returns the same artifact, leaving only the imported MP4
and remuxed MKV in managed storage.

The default workspace, including the saved sample, is removed on exit. Keep a
playable `sample.mkv` with:

```bash
uv run python -m mushin.remux_playground --output ./remux-output
```

The output directory is created if needed. Existing `sample.mkv` files are
never overwritten; choose another directory for another run. Source and
artifact workspaces are temporary even when the saved output is retained.
The sample requires the `lavfi` input device, `testsrc2`/`sine` filters,
MPEG-4/AAC encoders, and MP4/Matroska muxers. Missing tools or capabilities
produce an error and a nonzero exit status. FFmpeg installation and integration
test commands are documented below.

The original filename-only demo remains available and does not require FFmpeg:

```bash
uv run python -m mushin.playground
```

For the combined episode-naming workflow, run:

```bash
uv run python -m mushin.plex_playground --input "/path/to/episode.mkv" --output ./plex-output
```

This imports your episode and runs **import → remux to MKV →
guess episode metadata → template variables → filename → rename → save**.
It verifies source preservation, the saved bytes, container, codecs, and
duration. Omit `--output` for a disposable run. Existing files are never
replaced. An input file is required. Year and episode title
are optional; their parentheses or separator are omitted when unavailable.
Other required placeholders still report a missing-variable error.

`--source` is an alias for `--input`. The original file is unchanged. The
current template requires a series title, season, and episode number. Without
a year or episode title, it produces a name such as `Example Show - s01e02.mkv`.

When filenames contain only an episode number and episode title, configure
the `GuessVideo` node with the known series:

```python
config={"type": "episode", "series_title": "The Apothecary Diaries"}
```

This supplies a synthetic series-folder context to GuessIt without changing
the source or artifact filename. For example,
`S01E03-The Unsettling Matter of the Spirit [3B28F408].mkv` is parsed with
series title `The Apothecary Diaries` and episode title
`The Unsettling Matter of the Spirit`. The Plex playground and its exported
workflow use this context; change or remove `series_title` for other shows.
Unlike a template default, this context affects how the filename is parsed.

The template uses `s{season:02d}e{episode:02d}` to zero-pad integer values to
at least two digits. Values above 99 retain all digits. Plain placeholders
remain supported; other format specifiers and field traversal are rejected.

In the `TemplatedString` node's config, use `defaults` for missing variables
and `optional_sections` for fragments that should disappear with their values:

```python
config={
    "template": "{title}{year_suffix} - s{season:02d}e{episode:02d}{episode_suffix}.{container}",
    "defaults": {"title": "Unknown Show"},
    "optional_sections": {
        "year_suffix": " ({year})",
        "episode_suffix": " - {episode_title}",
    },
}
```

For example, `defaults={"year": 2024}` fills an absent year and includes
` (2024)`. Supplied values take priority over defaults. Each optional section
is included only when all of its variables exist and are nonempty; zero and
`False` count as present. Section names are reserved and cannot reference
other sections. The `ToVariables` adapter's `omit_none=True` removes unknown
metadata values before rendering, allowing defaults or omission to apply.

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

## Conversion progress

`WorkflowRunner.run(..., on_event=callback)` delivers `NodeProgressEvent`
notifications between a node's running and terminal state events. Nodes call
`context.report_progress(completed, total=None, unit="seconds")`. Events carry
run/node IDs, a UTC timestamp, completed work, optional total, and unit;
`event.percentage` returns a value capped at 100 or `None` for unknown totals.
Callbacks are synchronous; ordinary observer exceptions are logged.

`RemuxVideo` streams FFmpeg's machine-readable `-progress pipe:1` output
([FFmpeg documentation](https://ffmpeg.org/ffmpeg.html#toc-Main-options)).
It reports processed media seconds and uses the input's ffprobe duration as
the total when positive and known. Unknown duration displays elapsed media
seconds without a percentage. Updates are throttled to one per 250 ms, with
the final FFmpeg record delivered immediately. Media time does not regress.
FFmpeg's end record does not imply successful verification or publication;
use the node's terminal state to determine success. Cancellation suppresses
further progress while the process is terminated and reaped.

The remux and Plex playgrounds display these events. Fast remuxes may produce
only a couple of updates. A remux returning the original artifact performs no
conversion and emits no conversion progress.

`ProcessRunner.ffmpeg(..., on_progress=callback)` provides the same throttled
media seconds directly. This reserves stdout for progress and returns empty
captured stdout; stderr diagnostics remain bounded. The incremental parser
bounds line buffering and ignores malformed time values, so long conversions
do not accumulate progress output or hit the normal stdout capture limit.

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

## RemuxVideo

Register the node with your artifact store and discovered tools:

```python
from mushin.nodes import RemuxVideo

registry.register(RemuxVideo.spec, RemuxVideo(store, tools))
```

The `video.remux` node accepts an artifact on `file` and returns an artifact
on `file`. Its configuration requires `container` (`mkv`, `matroska`, `mp4`, or
`webm`), with optional `streams` (the stream policy above) and `timeout`
(positive seconds per subprocess, default 300). For example:

```yaml
type: video.remux
config:
  container: mkv
  streams:
    audio: all
    chapters: copy
  timeout: 300
```

The node probes the input, maps selected streams explicitly, and runs FFmpeg
with `-c copy` and an explicit output format. It never falls back to encoding.
Container compatibility is decided by the installed FFmpeg build; a rejected
combination raises `MediaToolError` with code `remux_failed` and retains the
bounded FFmpeg stderr diagnostic. Other process error codes are preserved.

`mkv` is an alias for the Matroska muxer and produces `.mkv` files. When the
input is already in the requested container and the stream policy retains
all existing streams and chapters, the node returns the **original artifact
reference**, including its filename, without running FFmpeg or publishing
another artifact. It still probes content; filename extensions do not decide
the container. A policy that actually removes streams or chapters remuxes as
usual. Reapplying the same policy to the resulting artifact returns it unchanged.

Because ffprobe reports shared container families, the no-op check distinguishes
Matroska from WebM using the EBML header's document type. MP4 is identified by
recognized MP4 major brands rather than the shared MOV/MP4 aliases. Unknown
headers or brands conservatively take the normal remux path.

Before publication, the node probes the output and verifies the container
family, stream count/order, codec/type, known dimensions/channel counts,
cover-art disposition, and chapter count/timing (50 ms tolerance). A mismatch
raises `remux_verification_failed` and discards the output. This is structural
verification, not a packet-by-packet comparison. ffprobe groups Matroska/WebM
and MOV/MP4 as container families; the FFmpeg output format is explicit.
Outputs that add auxiliary streams or change attachment representation are
rejected by the strict stream check.

Newly remuxed output uses the original filename stem with `.mkv`, `.mp4`, or
`.webm`. Source content stays unchanged. Failures and cancellation discard
temporary output after process cleanup. Workflow execution currently wraps
node exceptions in its generic `node_execution_failed` run error.

Run the node tests with:

```bash
uv run python -m unittest discover -s tests -p test_remux_video.py
```

The real-tool test generates a tiny video and checks successful remuxing and
incompatible-container cleanup. It skips when the required tools or fixture
capabilities are absent; the other tests use simulated tool responses.

Run the broader real-media integration suite with:

```bash
uv run python -m unittest discover -s tests -p test_remux_integration.py -v
```

It generates one-second fixtures in temporary directories; no media downloads
or binary fixtures are needed. It covers MP4/WebM/MKV conversion and
idempotency, two audio tracks, SubRip subtitles, a text attachment (including
extracted-byte comparison), PNG cover art, chapters, and explicit drops.
Failure tests check malformed media, unsupported configuration, missing
executables, and incompatible containers. Timeout and both cancellation
methods interrupt real FFmpeg and verify child reaping before staging cleanup,
unchanged source bytes, and no published output.

Install FFmpeg and ffprobe on PATH (for example, `sudo apt install ffmpeg` on
Ubuntu). Tests skip when tools or required fixture capabilities are absent;
review verbose output for skips. The fixtures use the `lavfi` input device,
`color`/`sine` filters, MPEG-4/AAC, VP8 (`libvpx`)/Opus (`libopus`), SubRip,
and PNG encoding. Child liveness checks require POSIX; other integration
checks also run on systems with compatible FFmpeg builds.

Run all tests, including integration tests when available, with:

```bash
uv run python -m unittest discover -s tests
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

Export any `Workflow` directly to a chosen YAML file:

```python
workflow.export("workflows/plex.author.yaml", form="author")
workflow.export("workflows/plex.raw.yaml", form="raw")

# Omit the path to get YAML text without writing a file.
author_yaml = workflow.export(form="author")

# Retain workflow/node UUIDs while still using named references.
workflow.export("workflows/plex.with-ids.yaml", form="author", include_ids=True)
```

Raw form is the full UUID-based snapshot and is the default. Author form uses
unique keys derived from node names (falling back to node types), adding
suffixes when names collide. It retains node configuration and display names.
Unnamed nodes acquire their generated author key as a display name on import.
Author form omits UUIDs by default, so importing it creates fresh identities;
`include_ids=True` retains them. Original author keys are not stored in a
`Workflow`, so export generates new keys rather than recovering them.

Both forms load with `load_workflow_file()`. Export does not require a node
registry or a runnable graph. Author export rejects duplicate node IDs and
references to missing nodes because named references would be ambiguous or
unresolvable; raw form can preserve those incomplete drafts.

File export creates parent directories and atomically replaces existing files;
it does not preserve YAML comments or hand formatting. Export is synchronous;
inside async code use `await asyncio.to_thread(workflow.export, path,
form="author")` to keep file operations off the event loop.

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

## Scan a folder

Register `ScanFolder` from `mushin.nodes` with
`registry.register(ScanFolder.spec, ScanFolder())`. The node type is
`folders.scan_folder`; its `path` input is a folder path string. Its `files`,
`folders`, and `symlinks` outputs each contain a `string_list` of sorted absolute
paths. Configuration is empty.

Scanning includes hidden entries directly inside the folder. Symlinks appear
only in `symlinks`, including broken links; their targets are not followed.
Special files are excluded. It does not recurse or import files
into artifact storage. Directory reads run in a worker thread; cancellation is
checked before scanning, between entries, and before returning results.
Missing folders, non-directory paths, and permission errors fail the node.
