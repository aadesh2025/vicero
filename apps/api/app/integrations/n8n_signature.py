"""Does an n8n workflow actually verify BotForge's webhook signature? (RISK-REGISTER R15)

BotForge signs every call it makes to an n8n webhook (`n8n_client.sign`), but n8n only checks
that signature if the workflow says to. A workflow built by hand in the n8n UI, or cloned before
the shipped templates gained the check, accepts an unsigned `curl` from anyone who learns its
URL — bypassing the agent, RBAC, budgets and every input check. This module inspects a workflow's
JSON (as returned by the n8n API) and says whether the check is really in the request path.

**Structural, not nominal.** It does not look for a node called "Verify BotForge signature" —
a name proves nothing. It requires, for every Webhook node:

1. the `rawBody` option is on (without it the HMAC cannot be recomputed);
2. the Webhook's ONLY outgoing connections go to a Code node whose source does the HMAC work
   (`createHmac`, the `x-botforge-signature` header, `timingSafeEqual`) — so no branch reaches
   the automation without passing through it;
3. that Code node's output goes only to an IF/Switch node that tests its `verified` result —
   a verifier whose answer is never consulted is decoration.

**What this is and is not.** It is a lint over workflow shape, enough to catch every honest
mistake (a hand-built workflow, an old clone, a template edited to drop the check). It is not
proof of correctness: someone determined could paste the marker strings into a comment. The
shipped verifier's behaviour is proven separately by `tests/test_n8n_workflows_signed.py`,
which runs the real code under Node against the Python signer.
"""

from __future__ import annotations

import json
from typing import Any

_WEBHOOK_TYPES = {"n8n-nodes-base.webhook"}
_CODE_TYPE = "n8n-nodes-base.code"
_GATE_TYPES = {"n8n-nodes-base.if", "n8n-nodes-base.switch"}
# What the shipped verifier's source contains (infra/n8n/*.json); all three must be present.
_VERIFIER_MARKERS = ("createHmac", "x-botforge-signature", "timingSafeEqual")

FIX_HINT = (
    "Start the workflow with the shipped verify chain "
    "(Webhook [rawBody on] -> 'Verify BotForge signature' Code node -> IF -> your automation) "
    "— see infra/n8n/README.md, or re-clone infra/n8n/template-starter-automation.json."
)


def _successors(connections: dict[str, Any], name: str) -> list[str]:
    """Every node a node's `main` outputs feed, across all output branches."""
    out: list[str] = []
    for branch in ((connections.get(name) or {}).get("main") or []):
        for link in branch or []:
            target = link.get("node") if isinstance(link, dict) else None
            if target:
                out.append(str(target))
    return out


def _is_verifier(node: dict[str, Any] | None) -> bool:
    if not node or node.get("type") != _CODE_TYPE:
        return False
    source = str((node.get("parameters") or {}).get("jsCode") or "")
    return all(marker in source for marker in _VERIFIER_MARKERS)


def _tests_verified(node: dict[str, Any] | None) -> bool:
    if not node or node.get("type") not in _GATE_TYPES:
        return False
    return "verified" in json.dumps(node.get("parameters") or {})


def unverified_reason(workflow: dict[str, Any]) -> str | None:
    """`None` if every Webhook in `workflow` is gated by a signature check; else why not."""
    nodes = [n for n in (workflow.get("nodes") or []) if isinstance(n, dict)]
    by_name = {str(n.get("name")): n for n in nodes}
    connections = workflow.get("connections") or {}
    hooks = [n for n in nodes if n.get("type") in _WEBHOOK_TYPES]
    if not hooks:
        return "it has no Webhook node, so there is nothing for BotForge to sign a call to"

    for hook in hooks:
        hook_name = str(hook.get("name"))
        options = (hook.get("parameters") or {}).get("options") or {}
        if options.get("rawBody") is not True:
            return f"Webhook '{hook_name}' does not have the 'Raw Body' option on, so the HMAC cannot be recomputed"
        targets = _successors(connections, hook_name)
        if not targets:
            return f"Webhook '{hook_name}' is not connected to a signature-verify node"
        for target in targets:
            if not _is_verifier(by_name.get(target)):
                return (
                    f"Webhook '{hook_name}' feeds '{target}' directly; every path from a Webhook "
                    "must go through a Code node that verifies the BotForge HMAC signature"
                )
            gates = _successors(connections, target)
            if not gates or not all(_tests_verified(by_name.get(g)) for g in gates):
                return (
                    f"the verify node '{target}' is not followed by an IF/Switch that tests its "
                    "`verified` result, so a bad signature would still reach the automation"
                )
    return None
