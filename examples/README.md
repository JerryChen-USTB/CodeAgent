# Examples

`task.yaml` is a runnable debug-only example for the non-interactive CLI path:

```bash
codeagent run --config examples/task.yaml
```

The example reads `debug_project/failing.log`, writes a fresh run directory under
`codeagent_runs/examples/`, and produces debugging plus final reports without
calling an LLM or modifying project files.
