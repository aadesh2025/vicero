# 06 — AI Engine (LLM Providers, RAG, Agent Runtime)

## 1. Provider abstraction (`apps/api/llm/`)

Design one interface, many providers. **Free-first ordering** by default.

```python
class ChatProvider(Protocol):
    name: str
    async def chat(self, req: ChatRequest) -> ChatResponse: ...
    async def stream(self, req: ChatRequest) -> AsyncIterator[StreamEvent]: ...
    def supports_tools(self) -> bool: ...
    async def list_models(self) -> list[ModelInfo]: ...

class EmbeddingProvider(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...
    dim: int
```

`ChatRequest` carries: messages, model, temperature, top_p, max_tokens, frequency_penalty,
presence_penalty, stop, tools (JSON schema), tool_choice, stream.

### Providers to implement (in this priority order)
1. **Groq** (`GROQ_API_KEY`) — OpenAI-compatible API, very fast; DEFAULT. Models e.g.
   e.g. `openai/gpt-oss-120b`, `openai/gpt-oss-20b` (discover via `/models` — Groq retires models without warning; see `llm/catalog.DEFAULT_CHAT_MODEL`).
2. **Google Gemini** (`GEMINI_API_KEY`) — has a free tier; native API. Models `gemini-1.5-flash` etc.
3. **Ollama** (`OLLAMA_BASE_URL`, default `http://ollama:11434`) — fully local/free; OpenAI-
   compatible endpoint. Models discovered from the running Ollama.
4. **OpenRouter** (`OPENROUTER_API_KEY`) — exposes many free models via OpenAI-compatible API.
5. **OpenAI** (`OPENAI_API_KEY`) — paid; `gpt-4o`, `gpt-4o-mini`, etc.
6. **Anthropic** (`ANTHROPIC_API_KEY`) — paid; Claude models via Messages API.
7. **Custom OpenAI-compatible** — user provides `base_url` + key; treat like OpenAI. The URL must be public
   unless the operator lists its host in `PROVIDER_PRIVATE_HOSTS` (ADR-087); checked at save time and on every request.

Because most of the above (Groq, Ollama, OpenRouter, OpenAI, custom) speak the
**OpenAI-compatible** protocol, implement one `OpenAICompatibleProvider` parameterized by
`base_url` + key, and thin subclasses for Groq/OpenRouter/Ollama/custom. Gemini and
Anthropic get dedicated adapters (translate messages/tools to their native shapes).

### Provider resolution & BYO keys
- Per-agent `model_config.provider` + `model` selects the provider.
- Key lookup order: agent-scoped `provider_credentials` → org-scoped default → platform env
  key. If none and provider requires a key → return `llm.provider_unavailable` (and, during
  build, stub with a canned response + loud log so the build isn't blocked — see CLAUDE §7).
- Encrypt all stored keys; never return them in full.

### Fallback chain (NFR-4)
Agent config may list `fallback_providers`. On timeout/5xx/rate-limit, try the next provider
with the mapped equivalent model. Record which provider actually served the request in usage.

### Cost & token accounting
Maintain a `PRICING` table (per provider/model, micros per 1K tokens; free providers = 0).
After each call, compute `cost_micros`, write to `messages` and roll up into `usage_records`.

## 2. RAG pipeline (`apps/api/rag/`)

### Ingestion (Celery task)
1. **Load**: by mime type — PDF (pypdf/pdfplumber), DOCX (python-docx), TXT/MD (plain),
   CSV (row/record aware), URL (fetch + readability extraction, respect SSRF rules).
2. **Clean/normalize**: strip boilerplate, keep headings/page metadata.
3. **Chunk**: recursive character/token splitter, `chunk_size` + `chunk_overlap` from KB
   config (defaults 800 tokens / 100 overlap). Attach metadata (page, heading, ordinal).
4. **Embed**: batch through the KB's `EmbeddingProvider`. Free-first embeddings:
   Ollama `nomic-embed-text` (dim 768) or Gemini embeddings; fallback OpenAI
   `text-embedding-3-small` (dim 1536). **Store the dim per KB**; the `chunks.embedding`
   vector column dimension must match the KB's model (create per-KB or use max dim + store
   model). Recommend: one embedding model per KB, migration sets vector dim, validate on write.
5. **Store**: insert chunks + vectors; set `document.status=ready`, `chunk_count`.
6. **Progress**: update status; emit WS/webhook `document.ready|failed`.

### Retrieval
1. Embed the user query with the same model as the KB.
2. Vector similarity (cosine) top-k from pgvector, filtered by `knowledge_base_id`s and
   `organization_id`, above `score_threshold`.
3. **Optional hybrid**: also run Postgres full-text (`ts_rank`) and merge via reciprocal
   rank fusion.
4. Assemble context block with source markers; keep within token budget; return citation set.
5. If nothing passes threshold → mark as "unanswered" (feeds analytics) and let the agent
   use its fallback message or general knowledge per config.

### Prompt assembly (order)
`[system persona + guardrails]` → `[retrieved context with citations]` →
`[long-term memory summary]` → `[recent message window]` → `[current user turn]`.
Enforce a token budget: reserve for completion, trim oldest history first, then reduce
retrieved context, never drop the system prompt.

## 3. Agent runtime / orchestration (`apps/api/app/chat/runtime.py`, `run_turn`)

Loop:
1. Build request (§2 assembly) with tool schemas if `features.tools_enabled`.
2. Stream from provider.
3. If the model emits a **tool call**: pause generation, execute via `tools.service`
   (built-in / HTTP / n8n / MCP), append tool result as a `tool` message, and run another
   provider pass. The loop is bounded by `settings.tool_max_iterations` (default 4) and, when the
   agentic runtime is on for the org (`chat.budget.agentic_loop_enabled`: platform flag AND explicit
   org opt-in), by an `AgentBudget` shared with nested calls — steps, tool calls, wall-clock
   seconds, cost (`agentic_max_*` settings). Whichever trips first ends the loop; it is sticky.
4. On completion: persist assistant message, citations, tool_runs, usage; update conversation
   `last_message_at`, memory.
5. Stream events to client per `04 §Streaming event contract`.

### Memory
- **Short-term**: last K turns within token budget.
- **Long-term**: when history exceeds a threshold, summarize older turns into
  `conversations.memory_summary` (a cheap free-model call) and drop raw turns from the prompt.
- Optional: semantic memory of past conversations (store summaries as vectors) — stretch.

### Guardrails
- Pre-input: block configured topics / basic prompt-injection heuristics on untrusted
  (retrieved/tool) content — never let it override the system prompt.
- Post-output: optional moderation hook; redact secrets.

## 4. Persona & model configuration surface (maps to FR-C)

Everything the UI exposes maps to `agent_versions`:
- **Persona**: display name, avatar, `system_prompt`, `character`, `role`, `tone`,
  `guardrails[]`, welcome message, fallback message, suggested prompts.
- **Model**: provider, model, temperature (0–2), top_p, max_tokens, frequency_penalty,
  presence_penalty, stop sequences.
- **RAG**: enabled, knowledge_base_ids, top_k, score_threshold, hybrid on/off.
- **Features**: tools_enabled, memory_enabled, handoff_enabled.
- **Credentials**: choose org-default key or agent-specific BYO key, or custom endpoint.

## 5. Tool calling (`apps/api/tools/`)

- **Built-in tools**: `knowledge_search` (query the agent's KB), `get_datetime`,
  `calculator`, `http_request` (guarded), `web_search` (stub/pluggable). Each declares a
  JSON schema.
- **HTTP tools**: user-defined; execute with SSRF guards, timeouts, header/body templating
  from arguments; return parsed JSON/text.
- **n8n tools**: bound to a workflow; call its webhook with structured args (see
  `07-INTEGRATIONS.md §n8n`). Sync mode awaits response; async mode returns a pending token
  and resolves via callback.
- Every execution logged to `tool_runs` (input, output, status, latency).

## 6. Testing hooks
Provide a `FakeProvider` (deterministic) and `FakeEmbeddingProvider` for tests so the whole
chat/RAG path is testable without network or real keys. Unit-test the fallback chain, token
budgeting, citation assembly, and the tool loop iteration cap.
