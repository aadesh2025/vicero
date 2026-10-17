"""Install the three booking tools for one agent: import the n8n workflows, register them, bind them as tools.

    python scripts/install_booking_tools.py --agent-id <uuid> --dir /tmp/n8n-workflows [--publish]

Run inside the api container (it reads N8N_API_KEY / N8N_BASE_URL from the environment and never prints them).
`--dir` is `infra/n8n/workflows/`. Idempotent: workflows are found by name and updated, the automation registry row and
the tool are updated in place, so re-running after editing a workflow JSON is the way to ship a change.

Per organisation it creates:
  * three n8n workflows named `ORG-<org id> | Booking | <tool>` (that prefix is what routes their failures to the org's
    run history), webhook paths `<org slug>-<tool>`, active, with the global error workflow attached;
  * three `automations` rows (staff registry, ADR for docs/26) tying each webhook to this org and agent;
  * three typed tools bound to the agent (`book_appointment`, `check_availability`, `get_customer`);
  * `features.tools_enabled = true` on the agent (published with `--publish`).

No credential is created or stored. The Google Calendar node inside `book_appointment` stays disabled until you attach
a Google credential to it in the n8n editor (docs/25 section 15).
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import sys
import uuid
from pathlib import Path
from typing import Any

SLUGS = ("book-appointment", "check-availability", "get-customer")
ERROR_WORKFLOW_NAME = "Vicero | Report failed run (global)"


def localize(
    template: dict[str, Any], *, org_id: uuid.UUID, org_slug: str, tool: str, slug: str, error_wf_id: str | None
) -> dict[str, Any]:
    """The template workflow for one organisation: its name, webhook path and error workflow. Pure, so it is tested."""
    wf = copy.deepcopy(template)
    path = f"{org_slug}-{slug}"
    wf["name"] = f"ORG-{org_id} | Booking | {tool}"
    for node in wf["nodes"]:
        if node["type"] == "n8n-nodes-base.webhook":
            node["parameters"]["path"] = path
            node["webhookId"] = path
    settings = dict(wf.get("settings") or {})
    if error_wf_id:
        settings["errorWorkflow"] = error_wf_id
    wf["settings"] = settings
    return wf


def webhook_path(wf: dict[str, Any]) -> str:
    node = next(n for n in wf["nodes"] if n["type"] == "n8n-nodes-base.webhook")
    return str(node["parameters"]["path"])


def upsert_workflow(client: Any, api: str, wf: dict[str, Any]) -> str:
    """Create or update by name, then activate. Returns the n8n workflow id."""
    listing = client.get(f"{api}/workflows", params={"limit": 250})
    listing.raise_for_status()
    existing = next((w for w in listing.json().get("data", []) if w.get("name") == wf["name"]), None)
    if existing:
        client.post(f"{api}/workflows/{existing['id']}/deactivate")
        resp = client.put(f"{api}/workflows/{existing['id']}", json=wf)
        wid = existing["id"]
    else:
        resp = client.post(f"{api}/workflows", json=wf)
        wid = resp.json().get("id") if resp.status_code < 300 else None
    if resp.status_code >= 300 or not wid:
        raise SystemExit(f"error: n8n refused workflow {wf['name']!r}: HTTP {resp.status_code} {resp.text[:200]}")
    act = client.post(f"{api}/workflows/{wid}/activate")
    if act.status_code >= 300:
        raise SystemExit(f"error: could not activate {wf['name']!r}: HTTP {act.status_code} {act.text[:200]}")
    return str(wid)


def load_templates(directory: Path) -> dict[str, dict[str, Any]]:
    out = {}
    for slug in SLUGS:
        path = directory / f"{slug}.json"
        if not path.exists():
            raise SystemExit(f"error: missing {path}")
        out[slug] = json.loads(path.read_text(encoding="utf-8"))
    return out


def import_workflows(
    api: str,
    key: str,
    templates: dict[str, dict[str, Any]],
    specs: list[dict[str, Any]],
    org_id: uuid.UUID,
    org_slug: str,
) -> list[tuple[dict[str, Any], dict[str, Any], str]]:
    """Import + activate the three workflows for one org. Returns `(spec, workflow, n8n id)` per tool."""
    import httpx

    out = []
    with httpx.Client(headers={"X-N8N-API-KEY": key, "Accept": "application/json"}, timeout=30) as client:
        listing = client.get(f"{api}/workflows", params={"limit": 250})
        listing.raise_for_status()
        error_wf = next((w["id"] for w in listing.json().get("data", []) if w.get("name") == ERROR_WORKFLOW_NAME), None)
        if error_wf is None:
            print("note: the global error workflow is not installed; failures will not reach the org's run history")
        for spec in specs:
            wf = localize(
                templates[spec["slug"]],
                org_id=org_id,
                org_slug=org_slug,
                tool=spec["name"],
                slug=spec["slug"],
                error_wf_id=error_wf,
            )
            wid = upsert_workflow(client, api, wf)
            out.append((spec, wf, wid))
            print(f"workflow {wf['name']} -> {wid} (active), webhook /webhook/{webhook_path(wf)}")
    return out


async def install(session: Any, agent_id: uuid.UUID, directory: Path, publish: bool) -> int:
    import os

    from sqlalchemy import select

    from app.core.config import settings
    from app.models import Agent, Automation, Membership, Organization, Tool, User
    from app.modules.agents import schemas as agent_schemas
    from app.modules.agents import service as agents
    from app.modules.bookings.schemas import TOOL_SPECS
    from app.modules.orgs.deps import OrgContext
    from app.tools import schemas as tool_schemas
    from app.tools import service as tools

    key = os.environ.get("N8N_API_KEY") or settings.n8n_api_key
    if not key:
        print("error: N8N_API_KEY is not set in this container", file=sys.stderr)
        return 1
    base = (os.environ.get("N8N_BASE_URL") or settings.n8n_base_url).rstrip("/")
    api = f"{base}/api/v1"

    agent = (await session.execute(select(Agent).where(Agent.id == agent_id))).scalar_one_or_none()
    org = await session.get(Organization, agent.organization_id) if agent else None
    owner = (
        (
            await session.execute(
                select(Membership).where(Membership.organization_id == org.id, Membership.role == "owner")
            )
        )
        .scalars()
        .first()
        if org
        else None
    )
    user = await session.get(User, owner.user_id) if owner else None
    if agent is None or org is None or owner is None or user is None:
        print(f"error: no agent {agent_id}, or its workspace has no owner", file=sys.stderr)
        return 1
    ctx = OrgContext(org=org, membership=owner, user=user)
    templates = load_templates(directory)

    installed = await asyncio.to_thread(
        import_workflows, api, key, templates, TOOL_SPECS, org.id, org.slug
    )  # n8n's API is plain blocking httpx: a handful of calls, once

    for spec, wf, wid in installed:
        path = f"/webhook/{webhook_path(wf)}"
        url = f"{base}{path}"
        # By webhook path first: an n8n that was rebuilt hands out new workflow ids for the same paths.
        row = (
            await session.execute(
                select(Automation).where(Automation.organization_id == org.id, Automation.webhook_path == path)
            )
        ).scalar_one_or_none() or (
            await session.execute(select(Automation).where(Automation.n8n_workflow_id == wid))
        ).scalar_one_or_none()
        if row is None:
            session.add(
                Automation(
                    organization_id=org.id,
                    agent_id=agent.id,
                    n8n_workflow_id=wid,
                    webhook_path=path,
                    name=wf["name"][:160],
                )
            )
        else:
            row.organization_id, row.agent_id, row.webhook_path, row.status = org.id, agent.id, path, "active"
            row.n8n_workflow_id, row.name = wid, wf["name"][:160]
        await session.flush()

        tool = (
            await session.execute(
                select(Tool).where(Tool.agent_id == agent.id, Tool.name == spec["name"], Tool.type == "n8n")
            )
        ).scalar_one_or_none()
        if tool is None:
            await tools.bind_n8n_workflow(
                session,
                ctx,
                tool_schemas.BindN8nRequest(
                    name=spec["name"],
                    workflow_id=None,
                    workflow_name=wf["name"],
                    webhook_url=url,
                    mode="sync",
                    agent_id=agent.id,
                    description=spec["description"],
                    input_schema=spec["input_schema"],
                ),
            )
            print(f"tool {spec['name']} bound to the agent")
        else:
            tool.description, tool.input_schema, tool.enabled = spec["description"], spec["input_schema"], True
            tool.config = {
                **tool.config,
                "workflow_id": wid,
                "workflow_name": wf["name"],
                "webhook_url": url,
                "mode": "sync",
            }
            print(f"tool {spec['name']} updated")

    latest = max(await agents.list_versions(session, ctx, agent_id), key=lambda v: v.version)
    if not latest.features.get("tools_enabled"):
        patched = await agents.update_version(
            session,
            ctx,
            agent_id,
            latest.version,
            agent_schemas.UpdateVersionRequest(features={**latest.features, "tools_enabled": True}),
        )
        print(f"agent version {patched.version}: tools enabled ({'published' if patched.is_published else 'draft'})")
        if publish and not patched.is_published:
            await agents.publish_version(session, ctx, agent_id, patched.version)
            print(f"agent version {patched.version} published")
    elif publish and not latest.is_published:
        await agents.publish_version(session, ctx, agent_id, latest.version)
        print(f"agent version {latest.version} published")
    return 0


async def run(agent_id: uuid.UUID, directory: Path, publish: bool) -> int:
    from app.db.session import SessionFactory

    async with SessionFactory() as session:
        code = await install(session, agent_id, directory, publish)
        if code == 0:
            await session.commit()
        return code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--agent-id", type=uuid.UUID, required=True)
    parser.add_argument("--dir", type=Path, default=Path("infra/n8n/workflows"))
    parser.add_argument("--publish", action="store_true", help="publish the agent version that enables tools")
    args = parser.parse_args(argv)
    return asyncio.run(run(args.agent_id, args.dir, args.publish))


if __name__ == "__main__":
    raise SystemExit(main())
