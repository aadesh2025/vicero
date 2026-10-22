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

## Activating (a person, never Claude)

Activation is the only step that makes a client workflow live. It needs a real terminal (`ssh -t`), refuses an active or
untagged workflow, and asks you to type the workflow name.

```
ssh -t vicero /opt/vicero/n8n-push.sh activate "<Client> - <Agent> - <Job>" --org <slug>
```

Worked example:

```
$ ssh -t vicero /opt/vicero/n8n-push.sh activate "Acme Co - Support Bot - Book Appointment" --org acme-co
About to ACTIVATE "Acme Co - Support Bot - Book Appointment" (id 123) for org "acme-co". Live webhook: /webhook/acme-co-support-bot-book-appointment
Type the workflow name to confirm: Acme Co - Support Bot - Book Appointment
Activated workflow 123.
```

Before you run it: the credentials in the manifest exist in n8n, and you tested the workflow in the editor with sandbox data.
Typing anything other than the exact name changes nothing. Every attempt adds a line to `/opt/vicero/n8n-push-state/history.log`.
