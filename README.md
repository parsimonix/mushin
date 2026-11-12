# mushin
Arbitrary workflow composer / validator.

See [the design document](DESIGN.md) for the proposed backend architecture and implementation milestones.

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
