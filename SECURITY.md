# Security model

ShawnoBot is a remote execution gateway. Treat its configuration and host account as
security-sensitive.

## Default protections

- Deny all senders until `allowed_user_ids` is configured.
- Require both an allowed sender and an allowed group for group commands.
- Ignore messages sent by bots.
- Expose named projects, named logs, and named tasks only.
- Execute configured argument arrays with `shell=False`.
- Resolve log paths inside their configured project root.
- Disable Codex until explicitly enabled globally and for a project.
- Run Codex with an explicit `read-only` or `workspace-write` sandbox.
- Apply timeouts, concurrency limits, output limits, and JSONL audit logging.
- Stop only jobs launched and tracked by ShawnoBot.
- Read only Codex session IDs explicitly allowlisted in `config.yaml`.
- Resume only explicitly allowlisted desktop sessions; never write to the same session
  concurrently from the desktop app and Feishu.
- Keep natural-language conversations in the configured Codex sandbox; use `read-only`
  by default.
- Treat NLU model output as untrusted: accept only a strict JSON shape, an explicit command
  allowlist, bounded arguments, and the existing command validators.
- Never return provider-generated NLU text directly to the messaging user. A non-command
  result is an internal task prompt that Codex must independently execute and verify.
- Explicit slash commands bypass NLU. When NLU is enabled, ordinary message text and
  configured project/task/log/session aliases are sent to the configured provider.
- Exclude system/developer instructions, reasoning records, and tool calls from session output.

## Deployment recommendations

Run the bot as a dedicated, non-administrator OS user. Give that account access only
to the project directories it must manage. Keep the machine firewall enabled, do not
expose an arbitrary command endpoint, rotate the Feishu App Secret if it is disclosed,
and review `data/audit.jsonl` regularly.

Before switching Codex to `workspace-write`, enable version control and test backups
for every writable project.

## Reporting

Do not include credentials, private source code, or raw logs in a public report.
