# Client workflows

One folder per org slug, one workflow per file. Pushed to the VPS with `ops/n8n-push.sh` only (CLAUDE.md §13).

```
infra/n8n/clients/<slug>/
  <agent>-<job>.json             the workflow (committed)
  <agent>-<job>.manifest.yaml    org/agent ids, credentials_needed, n8n_workflow_id, status
```

The copy of the manifest in git is the draft. After `push --apply`, the script writes the manifest with the real
`n8n_workflow_id` and `status` to `/opt/vicero/n8n-push-state/<slug>/` on the server (outside git); copy it back here in a
normal commit if you want git to show it. `history.log` in the same folder has one audit line per push and activation.
