# Demo knowledge base (AUROZEN AI / Vicero)

Markdown ingested into the demo agent's knowledge base by `apps/api/scripts/seed_demo_knowledge.py`. **Everything here is demo content**:
prices, hours and policies are placeholders for showing how the assistant answers, not a real offer. Edit the files, then re-run the
script (it replaces documents that have the same filename). This README is not ingested.

## Load it (production)

```
ssh vicero "cd /opt/vicero && git pull -q --ff-only && docker cp docs/demo-knowledge vicero-prod-api-1:/tmp/demo-knowledge"
ssh vicero "docker exec vicero-prod-api-1 python scripts/seed_demo_knowledge.py --agent-id <AGENT_UUID> --dir /tmp/demo-knowledge --publish"
```
The worker ingests the documents in the background (status `queued` then `ready` in the dashboard's Knowledge tab). Drop `--publish`
to leave the edited agent as a draft for review.
