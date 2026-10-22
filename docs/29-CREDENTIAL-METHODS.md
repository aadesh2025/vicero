# Credential methods for client workflows

Used in the "Credentials needed" table at the end of every client workflow build (CLAUDE.md §13, runbook §12).
Numbers match section 26.1 of the AUROZEN playbook.

## The 7 methods

1. **API key or token.** A person pastes the key into the credential in the n8n editor.
2. **OAuth sign-in.** A person clicks "Connect" in n8n and the client clicks Allow on the provider's page.
3. **Service account or app registration.** The client (or AUROZEN) creates it at the provider; a person adds the key file or app secret in n8n.
4. **Username and password style.** SMTP, database or SFTP: a person types host, user and password into the n8n credential.
5. **Webhook URL as the credential.** The URL is the secret (for example a Slack or Discord incoming webhook); a person pastes it into the n8n credential.
6. **Client enters the key in Vicero.** The client adds it in Vicero (Connection or API tool); the workflow never holds it.
7. **AUROZEN owns the account.** AUROZEN's own account is used; a person sets it up once in n8n.

## Rules for the table

- Columns: node, service, credential type, credential name, method (1 to 7).
- Credential name is always `ORG-<org_id> | <service>` (one per org and service).
- Claude never creates a credential and never sees a value. It only lists what a person must create.
- The same list goes in the workflow's manifest under `credentials_needed` (type and name only).
- Never put a key in a workflow's input, output, name or sticky note.
