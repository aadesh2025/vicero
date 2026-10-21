# Tools, MCP servers and webhooks: owner guide

For the person who runs a Vicero workspace. No code needed. Everything below uses placeholders: replace `<...>` with your own values and never paste real keys into chats or documents.

## What each one is

| Thing | Plain meaning | Use it when |
|---|---|---|
| **Connection** | A saved address and login for another service (its web address plus a key). Saved once, reused by many tools. | You want the agent to talk to your own system, e.g. `https://api.<your-service>.com`. |
| **API tool** | One action on a connection, e.g. "look up an order" or "delete a to-do". The agent calls it when a customer asks. | You know the exact request the service expects. |
| **MCP server** | A ready-made set of tools published by someone else. You add its address and Vicero lists the tools for you to switch on. | The service already offers MCP (e.g. a docs search). |
| **Outbound webhook** | Vicero tells your system when something happens (a chat starts, a handoff, a tool runs). | You want events in your own system or in n8n. |
| **Inbound hook** | Your system tells Vicero that something happened (an order shipped) so the agent can react. | You want your system to start or inform a conversation. |

## Where each one is in the app

1. **Connections:** Settings > **Connections**. Add name, base URL (`https://api.<your-service>.com`), authentication type and key. Keys are stored encrypted and never shown again. Set **Allowed hosts** so the key can only go to your service.
2. **API tools:** open an agent > **Tools** tab > turn on **Enable tools** > **API tools** > add. Pick the connection, the method (GET, POST, DELETE), the path (`/orders/{{id}}`) and the inputs the agent must ask the customer for. Use **Sample call** to test before saving.
3. **MCP servers:** same **Tools** tab > **MCP servers**. Paste the server address (`https://<mcp-server>/mcp`), save, wait for the tool list to load, then switch on only the tools this agent should have. A server is visible to the agent you bind it to, not to your other agents.
4. **Webhooks:** the **Tools** tab has per-agent **Outbound webhooks** and **Inbound hooks**. Organization-wide webhooks are at Settings > **Webhooks**.
5. **Approvals:** on any tool, switch on **Require approval**. When the agent wants to use it, the request waits in **Inbox > Tool approvals** until a person clicks **Approve** or **Decline**.
6. **What happened:** each tool shows **Recent calls** (status, time, what was sent). Secrets are hidden there.

## Safety rules built in

- The agent can never call an address outside the **Allowed hosts** of its connection, and never reaches private or internal addresses.
- A tool that needs approval cannot be switched to "no approval" by the agent itself.
- MCP answers are cut at 8,000 characters and a call stops after at most 30 seconds. Slow servers (10 to 20 seconds is normal on some hosts) still work but make the chat feel slow.
- Command-line (stdio) MCP servers can only be added by platform staff.
- Inbound hooks need a signature or a secret. In plain terms: your system signs `<unix-time>.<body>` with the hook secret (HMAC-SHA256, hex) and sends it in `X-Vicero-Timestamp` and `X-Vicero-Signature`. Requests older than 5 minutes are refused.

## Plan limits

| Plan | Connections | MCP servers | Outbound webhooks | Inbound hooks |
|---|---|---|---|---|
| Trial | locked | locked | locked | locked |
| Starter | locked | locked | locked | locked |
| Pro | 5 | 2 | 5 | 3 |
| Business | unlimited | unlimited | unlimited | unlimited |

"Locked" means the screen shows a lock and the server answers "upgrade your plan" (HTTP 402). Platform staff change a plan under Admin > Workspaces > **Change plan** (it is logged). If you hit a limit on Pro, remove one you no longer use or ask for Business.

## 5-minute live check (approval and follow-up message)

Not yet verified on the live site; do this once with a throwaway workspace. You must be logged in as the owner (staff) in your browser. Nobody else should type the production password.

1. Admin > Workspaces: create or pick a throwaway workspace and use **Change plan** to put it on **Pro** (add the note "live check").
2. In that workspace, Settings > Connections: add `https://<test-api>.example.com` with no authentication. On a new agent > Tools: add an API tool `delete_todo` (DELETE `/todos/{{id}}`, input `id` as a whole number) and switch on **Require approval**. In the Model tab turn tools on and set tool choice to "required first turn". **Publish** the agent (needs a verified email).
3. Open the agent's chat as a visitor and send: `Yes, I confirm: call delete_todo now for to-do number 7.`
4. Inbox > **Tool approvals**: click **Approve**. Expect the call to show `success` under Recent calls and, within a few seconds, a new assistant message in the conversation that reports the result. Send a second request and click **Decline**: nothing runs and the agent says so.
5. Delete the throwaway workspace (Settings > Organization > **Delete organization**). Write the date and result in `docs/25-PRODUCTION-RUNBOOK.md` section 19.

If the follow-up message does not appear, check that the agent is published and that a person has not taken over the conversation (a follow-up is not sent in that case).

More detail: `docs/28-AGENT-TOOLS-MCP-API-WEBHOOKS.md` (design), `docs/25-PRODUCTION-RUNBOOK.md` (operations).
