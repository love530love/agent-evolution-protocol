# Windows desktop wake pilot (WorkBuddy and Qoder)

Date: 2026-10-09. This is a **window-focus fallback**, not a conversation-delivery channel.

## Contract

1. Sticky sessions never start an executable or URI by default. `existing_window.ps1` requires one matching visible window and fails closed on zero or multiple matches.
2. Qoder must match the currently observed `D:\Programs\Qoder\Qoder.exe` process path and exact `Qoder` title. WorkBuddy's visible process currently hides `Path`; it must match process name `WorkBuddy`, exact title `WorkBuddy`, and be unique.
3. The helper may focus that window. `SetForegroundWindow` refusal is an adapter failure, not successful wake. It does not click, type, select a chat, or claim task delivery.
4. An explicit `allow_new_session=true` or session policy `allow` retains the URI route. Avoid this for routine collaboration.
5. If `AEP_WINCLI_PATH` points to a built `wincli.exe`, the helper additionally performs a **read-only** `window find --title` check. It never exposes general PowerShell/registry/keyboard tools to the collaboration daemon. CLI success only corroborates window discovery.
6. The target agent's `claim` or matching `task-state` is the first acceptance proof. A focused window is not an ACK.

## Current observations

- Read-only discovery found one current Qoder window and one WorkBuddy window.
- A focus attempt was accepted by Windows for WorkBuddy and rejected for Qoder. Neither task delivery nor agent acceptance was tested.
- `wincli` and `windows-mcp` were not installed. The upstream CLI currently needs .NET SDK 10; this machine had only SDK 9. An isolated SDK download attempt did not complete and was stopped. No system SDK or persistent MCP server was installed.
- Upstream CLI: <https://github.com/sbroenne/mcp-windows/blob/main/src/Sbroenne.WindowsMcp.Cli/README.md>. Its documentation explicitly distinguishes window/application observation from content delivery and states that all connected agents share one desktop.

## Acceptance gate before enabling UI injection

For each target application, in one supervised run: identify the exact existing window and chat, acquire `BROWSER_SESSION_LEASE`-equivalent desktop-input lease, observe the message field, inject a harmless task-ID-bearing test message, read the same chat to verify the text, and wait for agent ACK in the collaboration ledger. Release the lease in `finally`. If any step is ambiguous, stop without retrying input. Never infer ACK from process launch, focus success, or a successful click.
