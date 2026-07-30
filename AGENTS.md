# ShawnoBot repository guidance

- Preserve the deny-by-default authorization model.
- Never add an endpoint or command that executes user-provided shell text.
- Subprocesses must use argument arrays with `shell=False`.
- Resolve project files below their configured project root before reading them.
- Keep secrets in environment variables; never add real credentials to YAML or source files.
- Codex automation must keep an explicit sandbox and timeout.
- Session reading must remain ID-allowlisted and must never expose reasoning or system/developer messages.
- Add or update tests for authorization, path handling, command routing, and subprocess changes.
