# Commands and settings

Everything you can type, click or set, in one place. How each feature works is in [features.md](features.md).

- [Starting Sensai](#starting-sensai)
- [Chat commands](#chat-commands)
- [Settings](#settings)
- [Turning a feature off](#turning-a-feature-off)

## Starting Sensai

From the repository root, with the virtual environment active:

```bash
python3 sensai.py        # the chat in the terminal
python3 app.py           # the same chat in the browser, at http://localhost:8000
```

Options of `sensai.py`:

| Option | Default | What it does |
|---|---|---|
| `-m`, `--model` | `qwen3:8b` (`model` in `config.json`) | Chat model: any Ollama model that supports tools, or `openrouter/<model>` |
| `--persona` | `sensai` | Persona to start with; ignored when resuming |
| `-s`, `--system-prompt` | `system_prompt.txt` | System prompt file |
| `-d`, `--sessions-dir` | `sessions` | Where chats are saved |
| `-p`, `--profile` | `profile.json` | User profile; skipped if the default file is missing |
| `-r`, `--resume <id>` | | Continue a saved chat: history, system prompt, persona and model |
| `-l`, `--log-file` | `calls.jsonl` | Call log |
| `-y`, `--auto-approve` | off | Run tools that need confirmation without asking |
| `--no-guardrails` | off | No safety and privacy checks, for this run only |
| `--export <id> [--format json\|markdown\|pdf]` | `json` | Write a saved chat to a file, then exit |
| `--publish <id>` | | Create a read-only link to a saved chat, served by the web UI at `/s/<token>`, then exit |

## Chat commands

They work the same in the terminal and in the web UI's message box. In the web UI, typing `/` opens a menu of them.

| Command | What it does | In the web UI |
|---|---|---|
| `/help` | List the commands (web UI only) | |
| `/sessions` | List saved chats (CLI) | Sidebar, under Recent |
| `/exit` | Quit (CLI). Ctrl-C while the model answers stops just that answer | Stop button while it answers |
| `/stats` | Calls, errors, latency, tokens/s, fact-check flags, time saved by the cache, guardrail decisions, JSON retries | Sidebar → Stats |
| **Personas** | | |
| `/persona` | List the personas, `*` = the one answering | Persona badge next to the title |
| `/persona <name>` | Switch, keeping the conversation | Persona menu |
| `/persona show\|add\|edit\|delete …` | Show, create, change or delete a persona | Sidebar → Personas |
| **Structured output** | | |
| `/json <schema> <prompt>` | Answer as JSON matching the schema, with the attempts it took | `{ }` button next to the message box |
| `/schemas [show\|add\|delete …]` | List, show, add or delete schemas | Sidebar → JSON schemas |
| **Guardrails** | | |
| `/guardrails` | Show the rules | Settings → Guardrails |
| `/guardrails on\|off` | Every check on or off | Settings → "Check messages and answers" |
| `/guardrails pii <EMAIL\|PHONE\|IBAN\|CARD> <allow\|mask\|block>` | What happens to a kind of personal data | Settings → Personal data |
| `/guardrails refuse <off_topic\|harmful\|asks_for_others_data> on\|off` | Which messages are refused | Settings → "Refuse messages that are" |
| `/guardrails output on\|off` | The check that withdraws harmful or leaking answers | Settings → "Withdraw harmful or leaking answers" |
| `/guardrails message <text>` | The reply to a refused message | Settings → "Reply to a refused message" |
| **Semantic cache** | | |
| `/cache` | Settings, saved answers, hits and time saved | Sidebar → Saved answers |
| `/cache on\|off` | Reuse answers to similar questions or not | Settings → Semantic cache |
| `/cache list`, `/cache delete <id>`, `/cache clear` | See or forget saved answers | Sidebar → Saved answers |
| `/cache threshold <0-1>`, `/cache verify on\|off`, `/cache mid-chat on\|off` | How close a question must be, whether the model confirms it's the same, and whether mid-chat questions are looked up | Settings → Semantic cache |
| **Knowledge bases (RAG)** | | |
| `/rag on\|off` | Answer from the documents or not | Same command in the message box |
| `/kb`, `/kb create\|use\|model\|chunk\|try\|delete …` | Manage knowledge bases, preview retrieval | Sidebar → Knowledge bases |
| `/ingest`, `/docs` | Index the documents that changed, list what is indexed | Sidebar → Knowledge bases |
| **Files and tools** | | |
| `/permissions [set <path> <none\|read\|write> \| remove <path> \| default <level>]` | What the assistant may read or write in `safe_test/` | Sidebar → File access |
| | Connect, disable, restart, add or remove MCP servers; see the recent tool calls | Sidebar → Tools |
| **Prompt and branches** | | |
| `/prompt [versions\|show <n>\|rollback <n>\|compare <n1> <n2> <question>]` | System prompt versions | Sidebar → Prompt versions |
| `/edit <n> <text>` | Replace message `n` and answer again, as a new branch | Pencil on a message |
| `/branches [switch <id>]` | List or switch branches | Branch button next to the title |

## Settings

Changed in the app, settings apply to the next message and are saved, so they survive a restart. The files can also be edited by hand while the app is stopped.

### `config.json`

Created and updated by the app (Settings panel, `/cache`, `/guardrails on|off`, `/rag`). Main fields:

| Field | Default | Changed from |
|---|---|---|
| `model` | `qwen3:8b` | Settings → Model, `-m` |
| `check_model` | `qwen3:8b` | file: the local model that runs the guardrail, cache and JSON checks when the chat model is on OpenRouter |
| `openrouter_models` | `nemotron-3-ultra-550b-a55b:free` | file: the OpenRouter models offered in Settings |
| `num_ctx` | `16384` | file: context window sent to Ollama |
| `auto_approve` | `false` | Settings → Auto-approve tools, `-y` |
| `no_guardrails` | `false` | `/guardrails on\|off`, Settings, `--no-guardrails` (this run only) |
| `persona` | `sensai` | file: the persona new chats start with |
| `max_tool_rounds` | `5` | file: tool calls the model may chain in one answer |
| `sandbox` | `docker` | file: `docker` (isolated) or `subprocess` (fallback) for `run_python` |
| `sandbox_image` | `python:3.12-slim` | file |
| `rag_enabled` | `false` | `/rag on\|off` |
| `rag_collection` | `default` | `/kb use`, Knowledge bases panel |
| `rag_top_k`, `rag_min_score` | `4`, `0.5` | file: how many chunks are retrieved, and the similarity they need |
| `rag_chunk_size`, `rag_chunk_overlap`, `rag_embed_model` | `800`, `100`, `nomic-embed-text` | `/kb chunk`, `/kb model` per knowledge base |
| `cache_enabled` | `false` | `/cache on\|off`, Settings |
| `cache_threshold` | `0.7` | `/cache threshold`, Settings |
| `cache_verify`, `cache_mid_chat` | `true`, `true` | `/cache verify`, `/cache mid-chat`, Settings |
| `cache_embed_model` | `nomic-embed-text` | file |

### Other files

| File | What it holds | Changed from |
|---|---|---|
| `.env` | `OPENROUTER_API_KEY`, only for `openrouter/...` models | file |
| `guardrails/config/guardrails.json` | Action per personal data type, refused categories, refusal message, answer check | `/guardrails`, Settings → Guardrails |
| `hallucination/config/hallucination.json` | Fact-check on or off (`enabled`), `judge_model`, `confidence_threshold`, `action` (`warn`, `regenerate` or `refuse`), self-consistency | file, read at start; the web UI edits `enabled`, `confidence_threshold` and `action` in Settings → Fact-check and saves them here |
| `tools/mcp/config/mcp_servers.json` | MCP servers, which of their tools need confirmation, `disabled` | Sidebar → Tools |
| `tools/permissions.json` | File access rules inside `safe_test/` | `/permissions`, Sidebar → File access |
| `personas/*.json` | One persona per file | `/persona add\|edit\|delete`, Sidebar → Personas |
| `schemas/*.json` | JSON schemas for `/json` | `/schemas add\|delete`, Sidebar → JSON schemas |
| `profile.json` | Your name and preferences | Sidebar → Profile |
| `system_prompt.txt` | Base instructions, versioned in `prompt_versions/` | file, `/prompt rollback`, Sidebar → Prompt versions |

## Turning a feature off

Each feature can be turned off on its own; the rest keep working.

| Feature | How to turn it off | What happens then |
|---|---|---|
| Guardrails (all checks) | `/guardrails off`, Settings, or `--no-guardrails` | Messages and answers go through unchecked and unmasked |
| Answer check only | `/guardrails output off` | Messages are still checked and masked |
| Semantic cache | `/cache off` (off by default) | Every question goes to the model |
| Knowledge bases (RAG) | `/rag off` (off by default) | Answers come from the model and the tools only |
| Fact-check | Web UI: Settings → Fact-check off. Terminal: `"enabled": false` in `hallucination/config/hallucination.json`, then restart | Answers are not checked for unsupported claims |
| An MCP server | Sidebar → Tools → Disable, or `"disabled": true` in `mcp_servers.json` | Its tools disappear from the model's list; the others stay |
| Tool confirmations | Settings → Auto-approve tools, or `-y` | Tools run without asking; every call is still logged |
| Code sandbox isolation | `"sandbox": "subprocess"` | `run_python` runs in a plain process, not isolated from the machine (also the automatic fallback when Docker is missing) |
| File writes | `/permissions default read`, or `none` | The assistant can still read, or nothing at all |
| Profile | Clear it in Sidebar → Profile, or remove `profile.json` | The system prompt no longer mentions you |
| Personas | Stay on `sensai`, the general assistant | |

The call log (`calls.jsonl`) and prompt versioning are always on: they are how the rest is monitored and rolled back. Point `-l` to another file to keep a log apart. Pending (#84): a setting to turn off logging the content of messages.
