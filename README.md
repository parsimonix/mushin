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
