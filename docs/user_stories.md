# User stories

All stories follow the use case in [use_case.md](use_case.md): a developer working alone on a long-term project, who needs a private assistant on their own laptop. Every feature below is implemented, has at least two stories, and at least one criterion where something goes wrong (bad input, an error, a refusal). Each acceptance criterion can be shown live, except the ones marked **Pending (#NN)**: they belong to an issue that is still open, and become live when it is merged. The demo is in [demo.md](demo.md).

Each feature names the files that implement it and links to how it works in [features.md](features.md).

## [B1] Functional CLI chatbot

Code: [sensai.py](../sensai.py), [model/ollama.py](../model/ollama.py), [core/agent.py](../core/agent.py)

**Chat with a local model**

As a developer, I want to chat with a model running on my own laptop from the terminal, so that I get help without sending my code to an online service.

Acceptance criteria:
- `python3 sensai.py` starts a chat with the model chosen with `-m` or in `config.json`, without changing the code
- Answers appear progressively while the model writes them
- Earlier messages of the session are sent as context: the model can refer to what I said before

**Clear errors instead of crashes**

As a developer, I want a clear message when something is wrong, so that I know how to fix it instead of reading a traceback.

Acceptance criteria:
- If Ollama isn't running, Sensai says it cannot reach Ollama and asks whether it is running
- A model that isn't installed gives the `ollama pull <model>` command; a model that can't use tools says so and suggests one that can
- An empty message is not sent: Sensai prints a hint and waits for the next one

## [M1] Session persistence & user profile

Code: [storage/session_store.py](../storage/session_store.py), [prompting/prompt_builder.py](../prompting/prompt_builder.py) · How it works: [features.md](features.md#m1-sessions-and-profile)

**Resume a conversation**

As a developer working on the same project over several days, I want to resume a previous conversation, so that I don't have to re-explain my code and what we already tried.

Acceptance criteria:
- After each answer, the conversation is saved to `sessions/session_<id>.json`
- `/sessions` (or the web sidebar) lists the saved chats; `python3 sensai.py --resume <id>` restores one, and asking "what did I tell you earlier?" gets an answer based on the previous turns
- A chat can be renamed or deleted from the web sidebar
- An unknown id prints `session '<id>' not found` and exits without crashing

**Persistent profile**

As a developer, I want my name, preferences and instructions applied automatically, so that I don't have to repeat my language, level and how I like explanations at every new session.

Acceptance criteria:
- `profile.json` (name, preferences, custom instructions) is added to the system prompt at startup, and can be edited in the web UI under Profile
- In a new session, without being told, the assistant uses my name and follows the preferences from the profile
- Another profile can be used with `-p other.json`; a missing or invalid file shows a clear error

## [T1] Tool calling & MCP

Code: [core/agent.py](../core/agent.py), [tools/registry.py](../tools/registry.py), [tools/executor.py](../tools/executor.py), [tools/mcp/MCP_manager.py](../tools/mcp/MCP_manager.py) · How it works: [features.md](features.md#t1-tool-calling-and-mcp)

**Exact calculations with tools**

As a developer estimating sizes, durations or costs, I want the assistant to use tools instead of guessing, so that the numbers I put in my project are exact.

Acceptance criteria:
- "What is 1234 times 5678?" makes the model call the `multiply` tool and answer 7006652
- Several tool calls can be chained in one answer: "Using your tools, add 3 and 4, then multiply the result by 10" calls `add` then `multiply` and gives 70
- Each call shows up as a block in the chat (tool, server, arguments, result, time) and is written to `calls.jsonl`
- An unknown tool or invalid arguments go back to the model as an error and the chat keeps going; after 5 tool rounds in one answer, the turn stops with `Maximum tool-call rounds exceeded` and the next question works normally

**Plug in tool servers without code**

As a developer, I want the assistant to use MCP servers, so that I can give it access to files, the time, the web or other programs by editing a config file instead of writing code.

Acceptance criteria:
- The servers in `tools/mcp/config/mcp_servers.json` start with the chat, and their tools are offered to the model as `<server>_<tool>` (for example `filesystem_read_text_file`)
- In the web UI, Tools lists the servers and their tools, and can add, disable, enable, restart or remove a server without restarting Sensai
- A third-party server works the same way: with Blender open, "In Blender, add a red sphere next to the cube" calls `blender_execute_blender_code` after approval
- A server that can't start (for example when `uvx` is not installed) is skipped, the reason is written to `mcp.log`, and the chat still works

## [T3] Web search

Code: [tools/mcp/servers/web_server.py](../tools/mcp/servers/web_server.py) · How it works: [features.md](features.md#t3-web-search)

**Search for recent information**

As a developer using a library that changed after the model was trained, I want the assistant to search the web, so that its answers are not limited to outdated knowledge.

Acceptance criteria:
- A question about something recent makes the model call `web_search`, which returns up to 5 results with title, URL and snippet, and the answer lists its sources
- Snippets are cut to 300 characters, so a search doesn't flood the conversation
- If the search fails (for example without a network connection), the error goes back to the model and the chat continues

**Read a documentation page**

As a developer, I want the assistant to read a page from a URL, so that it can answer from the actual documentation instead of a search snippet.

Acceptance criteria:
- `web_fetch` returns the main text of the page, without menus and scripts, cut to 3000 characters
- A page that can't be downloaded returns an error to the model, which says it couldn't read it
- `web_search` and `web_fetch` are Sensai's own MCP server, written with FastMCP and started from the same config as the third-party servers

## [A3] Human-in-the-loop confirmations

Code: [sensai.py](../sensai.py) (`make_tool_confirmer`), [web/static/js/confirm.js](../web/static/js/confirm.js), [tools/mcp/config/mcp_servers.json](../tools/mcp/config/mcp_servers.json) · How it works: [features.md](features.md#a3-tool-confirmations)

**Approve or refuse a risky action**

As a developer who lets the assistant work with my project files, I want to approve or refuse a risky action before it runs, so that nothing is written or run without my consent.

Acceptance criteria:
- Before running a tool marked as requiring confirmation (file writes, `run_python`, Blender code), Sensai shows its name and arguments and asks yes / no / edit, in the terminal and as a dialog in the web UI
- Answering no sends "user refused to run this tool" back to the model as the tool result, and the chat continues normally
- A tool not marked as requiring confirmation runs without asking

**Review what was approved or refused**

As a developer, I want every confirmation decision recorded, so that I can review later what ran and what didn't.

Acceptance criteria:
- Every decision (approved, refused, edited, auto-approved, denied) is written to the call log with the tool name and arguments, and shown in the web UI's Tools panel under recent calls
- `-y` / Auto-approve in Settings runs flagged tools without asking, still logging the decision as "auto-approved"
- Editing an argument logs the edited value, not the original one

## [T2] Sandboxed code execution

Code: [tools/sandbox.py](../tools/sandbox.py), [tools/builtin.py](../tools/builtin.py) · How it works: [features.md](features.md#t2-sandboxed-code-execution)

**Code that actually runs**

As a developer, I want the assistant to run the code it writes and fix its own errors, so that I get code that works instead of code that looks right.

Acceptance criteria:
- "Use run_python to compute 2**100 and print it" asks for confirmation, runs the code and answers 1267650600228229401496703205376
- When the code fails, the error goes back to the model, which corrects the code and runs it again
- The call block shows where the code ran (`docker` or `subprocess`), its exit code and its output

**Protect my machine from generated code**

As a developer, I want generated code isolated from my files and network, so that a mistake can't damage my project.

Acceptance criteria:
- With Docker, the code runs in a throwaway container with no network and none of my files; it sees only the container's own files
- An endless loop is stopped after 5 seconds with `timed out after 5s`, and code over 128 MB is killed with `killed: the code went over the 128 MB memory limit`
- Without Docker or without the image, the code still runs in a plain subprocess, and the result says why it isn't isolated (for example `the image python:3.12-slim is not downloaded (run: docker pull python:3.12-slim)`)

## [T4] File access within permissions

Code: [tools/permissions.py](../tools/permissions.py), [tools/executor.py](../tools/executor.py), [tools/permissions.json](../tools/permissions.json) · How it works: [features.md](features.md#t4-file-access)

**Work on my project's files only**

As a developer, I want the assistant to read and change files in its workspace only, with rules per folder, so that it can help with real files without access to the rest of my machine.

Acceptance criteria:
- `tools/permissions.json` gives each path inside `safe_test/` `none`, `read` or `write`; `/permissions` or the File access panel changes them, and the next tool call uses the new rules
- Reading a file under a `read` path works; writing there, or touching a `none` path, is refused before the call runs, and the model is told why
- A path outside the workspace, like `/path/to/project`, or one that escapes it with `..` or a symlink, is refused

**Know what the assistant touched**

As a developer, I want every file operation recorded, so that I can check what was read, changed or refused.

Acceptance criteria:
- Every file call appears in the chat as a block and in `calls.jsonl`, with its decision (approved, refused, denied)
- Writes ask for confirmation first, even inside a `write` path
- The rules are listed in the system prompt, so the model knows what it may touch

## [R1] Knowledge bases (RAG)

Code: [rag/](../rag/), [rag/collections.py](../rag/collections.py), [web/static/js/kb.js](../web/static/js/kb.js) · How it works: [features.md](features.md#r1-knowledge-bases-rag)

**Ask questions about my project's documents**

As a developer, I want the assistant to answer from my project's own documents (specifications, notes, READMEs), so that its answers are based on my project instead of general knowledge.

Acceptance criteria:
- Putting `.txt`, `.md` or `.pdf` files in the knowledge base's folder (or uploading them in the web UI) and running `/ingest` indexes them; `/docs` lists each document with its number of chunks
- With `/rag on`, answers cite where each fact comes from, like `[spec.md, chunk 3]`
- When the documents don't cover the question, the assistant says so instead of presenting a guess as coming from them
- `/rag on` with an empty knowledge base gives a clear error asking to run `/ingest` first

**Several knowledge bases, each tuned to its documents**

As a developer with very different documents (a contract, a FAQ), I want separate knowledge bases with their own settings, and to see how retrieval works, so that each one gives good excerpts.

Acceptance criteria:
- `/kb create <name> [embed_model [size overlap]]` creates a knowledge base with its own folder, index, embedding model and chunking; `/kb use` switches the chat to it; `default` can't be deleted
- `/kb try <name> <question>` (or "Try a question" in the web UI) ranks every chunk by similarity, marks which pass the threshold, and shows the exact prompt that would be sent
- Running `/ingest` again only re-embeds the added and changed files; changing a knowledge base's embedding model or chunking rebuilds its index on the next `/ingest`

## [T5] Structured output

Code: [structured.py](../structured.py), [core/commands.py](../core/commands.py) (`run_json_command`, `run_schemas_command`), [web/static/js/json-mode.js](../web/static/js/json-mode.js) · How it works: [features.md](features.md#t5-structured-output-json-replies)

**Get answers as data**

As a developer writing scripts around the assistant, I want its answers in a fixed JSON format, so that my code can read them reliably.

Acceptance criteria:
- `/json bug_report <description>` (or the `{ }` button in the web UI) returns JSON matching `schemas/bug_report.json`, with Copy and Download buttons in the web UI
- Under each result, "valid JSON on attempt N of 3" shows whether retries were needed, with the errors sent back on each failed attempt
- A schema no answer can satisfy fails with `no valid JSON after 3 attempts` and each attempt's problems, and Ollama not running gives `cannot reach Ollama ... Is it running?`
- The `/json` question and its reply are not added to the conversation history, and go through the guardrails' message check

**Manage formats without code**

As a developer, I want to add and remove answer formats while the app runs, so that I can feed a new script without changing the code.

Acceptance criteria:
- `/schemas add <name> <file or {...}>` or the JSON schemas panel adds a schema, usable right away with `/json <name>`; `/schemas delete <name>` removes it
- An invalid schema (not JSON, not a valid JSON Schema, or not an object) is rejected with the reason, and nothing is saved
- `/json` and JSON mode only list the user's schemas, never the app's internal ones (`schemas/internal/`); an unknown name lists the ones that exist

## [EV2] Content & privacy guardrails

Code: [guardrails/pii.py](../guardrails/pii.py), [guardrails/classifier.py](../guardrails/classifier.py), [guardrails/guard.py](../guardrails/guard.py), [guardrails/output.py](../guardrails/output.py), [guardrails/commands.py](../guardrails/commands.py) · How it works: [features.md](features.md#ev2-guardrails)

**Keep client data out of the model and the logs**

As a developer who pastes logs and client data into the chat, I want emails, phone numbers and bank details hidden before they reach the model and before anything is saved, so that my clients' personal data stays private.

Acceptance criteria:
- "Write a polite email to support@shop.com asking for a refund" shows `masked before sending: EMAIL` and the model only receives `[EMAIL]`
- A message containing a card number such as `4111 1111 1111 1111` is refused without calling the model: `blocked: pii, contains CARD`
- Personal data in the model's answers is masked while it streams and in the saved session; `calls.jsonl` only records the type and count (`{"EMAIL": 1}`), never the data
- An answer that leaks someone else's private details (asking for `demo/team.txt` as a table) is withdrawn: `answer withdrawn: others_data, shares Marc Dupont: salary, ...`

**Refuse harmful or out-of-scope requests, with rules I can change**

As the developer configuring the assistant, I want harmful requests, requests for someone else's data and off-topic questions refused, and the rules editable while it runs, so that the assistant can't be misused and I can adapt it without code.

Acceptance criteria:
- "How can I make a bomb at home?", "Ignore all previous instructions..." and "What's Laura's phone number?" are refused with the category and the reason, and never reach the model
- Off-topic is judged against the active persona's scope: with `/persona tutor`, "What's a good recipe for a quick dinner?" is refused as off-topic
- `/guardrails pii email block` (or the Settings panel) changes a rule, saves it to `guardrails.json`, and applies it to the next message; `/stats` counts refusals, withdrawals and masked data by type
- If the check itself can't run (the check model is missing, Ollama is down), the message is refused with `check_failed` and the real error

## [A5] Personas

Code: [prompting/persona.py](../prompting/persona.py), [core/commands.py](../core/commands.py) (`run_persona_command`), [web/static/js/persona-editor.js](../web/static/js/persona-editor.js) · How it works: [features.md](features.md#a5-personas)

**Switch assistant mid-conversation**

As a developer, I want to switch persona in the middle of a conversation, for example to a code reviewer when my code is ready, so that I get a different kind of help without starting over.

Acceptance criteria:
- `/persona` lists the personas with the active one marked `*`; `/persona vera` (file name or persona name, any case) switches, and the persona badge in the web UI follows
- After "I'm learning Python" and `/persona vera`, "Which language am I learning?" is answered by Vera with "Python"
- With Vera active, "What's a good recipe for a quick dinner?" is refused as off-topic by her scope
- The persona is saved with the chat: `--resume` or reopening it in the web UI brings Vera back; an unknown or broken persona gives a clear error and keeps the current one

**Create and edit assistants without code**

As the developer configuring the assistant, I want to create, edit and delete personas while it runs, so that I can turn the same system into a different assistant without code.

Acceptance criteria:
- The Personas panel or `/persona add|edit|delete` creates, changes or removes a persona; a new one is in the menu right away, and editing the active one applies to the next answer
- Each field changes the answers: the name and role in "Introduce yourself", the scope in what is refused, the tone and the instructions in how it answers
- A persona with an empty field, an unknown field or a name already used is rejected with the reason; the active and the default persona can't be deleted

## [A6] Semantic cache

Code: [cache/service.py](../cache/service.py), [cache/semantic_cache.py](../cache/semantic_cache.py), [cache/check.py](../cache/check.py), [cache/commands.py](../cache/commands.py) · How it works: [features.md](features.md#a6-semantic-cache)

**Instant answers to repeated questions**

As a developer who often asks similar questions, I want them answered from a cache, so that I don't wait for the model again.

Acceptance criteria:
- A question that means the same as one already answered gets the saved answer in about a second, with "answered from cache in 1.2 s (saved 17 s)" under it
- Similar but different questions ("Celsius to Fahrenheit" / "Fahrenheit to Celsius", "Australia" / "Austria") are never mixed up: the model checks they are the same question
- A follow-up that depends on the chat ("and in Paris?"), answers that used tools or documents, and anything containing personal data are never served from or saved to the cache, and the note under the answer says why

**Control the cache**

As a developer, I want to see, tune and clear the cache, so that it never serves an outdated answer.

Acceptance criteria:
- `/cache on|off`, `/cache threshold`, `/cache verify` and `/cache mid-chat` (or Settings) change it for the next question
- `/cache list` and the Saved answers panel show each saved answer with its hits and time saved; `/cache delete <id>` and `/cache clear` forget them
- Saved answers are never shared between models, personas, profiles or prompt versions, and expire every day; an answer withdrawn by the guardrails is removed from the cache

## [EV1] Hallucination detection & grounding

Code: [hallucination/](../hallucination/) · How it works: [features.md](features.md#ev1-hallucination-check)

**Don't trust an invented fact**

As a developer relying on the assistant's answers about my project, I want each answer checked against what the assistant actually knew, so that I see which claims are unsupported instead of trusting an invented fact.

Acceptance criteria:
- After the answer has streamed (in the terminal chat and in the web UI), Sensai splits it into claims, checks each one against the conversation, the tool results and the document excerpts, and shows the verdict below the answer
- Asked about a made-up fact (like "the 2019 Nobel Prize in Mathematics"), the assistant says it doesn't know, or the invented claims are listed as unsupported
- Every check is written to `calls.jsonl` with each claim, its verdict, evidence and confidence, and the action taken; `/stats` shows how many answers were flagged

**Choose what happens to an unsupported answer**

As a developer, I want to choose whether unsupported answers are only flagged, rewritten or withheld, so that I can trade speed for safety.

Acceptance criteria:
- `hallucination/config/hallucination.json` sets whether the check runs, the judge model, the confidence threshold and the action: `warn`, `regenerate` or `refuse`
- `regenerate` writes the answer again once with the judge's feedback and checks it again; `refuse` replaces it with the refusal message, on screen and in the saved session
- If the check cannot run (Ollama down, no valid JSON from the judge), Sensai prints "answer not checked" and the chat goes on
- In the web UI, Settings → Fact-check sets the same three things (on/off, confidence threshold, action) and applies them to the next answer
- An answer served from the semantic cache is not checked again; `python3 -m evals.eval_hallucination -m <model> ... -p <versions>` prints the scores of each model and prompt version, compared in the README

## [A4] Prompt versioning

Code: [prompting/](../prompting/), [core/commands.py](../core/commands.py) (`run_prompt_command`), [web/static/js/prompts.js](../web/static/js/prompts.js) · How it works: [features.md](features.md#a4-prompt-versioning)

**Change the system prompt safely**

As a developer tuning the assistant's instructions, I want every version of the system prompt saved automatically, so that I can go back if a change makes the answers worse.

Acceptance criteria:
- Changing `system_prompt.txt` and starting a new chat saves a new version, listed with its date by `/prompt versions` and the Prompt versions panel
- `/prompt rollback <n>` restores version `n` in the file and in the current conversation right away
- Each line of `calls.jsonl` records which prompt version answered

**Compare two prompts**

As a developer, I want to ask the same question with two prompt versions, so that I can see which one answers better before keeping it.

Acceptance criteria:
- `/prompt compare 1 2 <question>` shows both replies, each with its latency (side by side in the Prompt versions panel)
- A version that doesn't exist gives a clear error instead of a reply
- Comparing doesn't change the current conversation

## [X2] Branching: resume & interrupt

Code: [storage/branching.py](../storage/branching.py), [core/commands.py](../core/commands.py) (`run_edit_command`, `run_branches_command`), [web/static/js/branches.js](../web/static/js/branches.js) · How it works: [features.md](features.md#x2-branching-resume--interrupt)

**Try another approach without losing the first**

As a developer unsure between two solutions, I want to edit an earlier message and get a new answer, so that I can compare approaches without losing the original conversation.

Acceptance criteria:
- `/edit <n> <text>` (or the pencil on a message) forks a new branch and the model answers the edited message right away
- `/branches` lists the branches with the current one marked; switching brings back that branch's full history, and the web UI shows "‹ 2/3 ›" under an edited message and a branch tree
- The original branch is never modified, and branches are kept when the chat is resumed; an `/edit` with a message number that doesn't exist gives a clear error

**Stop an answer that goes wrong**

As a developer, I want to stop a reply that is going in the wrong direction, so that I can redirect the assistant without waiting for the end.

Acceptance criteria:
- Ctrl-C in the terminal, or Stop in the web UI, cancels the reply while the model is answering
- My message is kept in the history, marked as interrupted, and saved right away
- The next message continues the conversation normally, with the interrupted message as context

## [EV3] Logging & monitoring

Code: [call_tracking/call_log.py](../call_tracking/call_log.py), [web/static/js/stats.js](../web/static/js/stats.js) · How it works: [features.md](features.md#ev3-call-log-and-stats)

**Track performance**

As a developer running the assistant on my laptop, I want to see how many calls were made, how fast they were and how many failed, so that I can spot problems quickly.

Acceptance criteria:
- `/stats` and the Stats panel show the calls, the errors, the average latency and the tokens per second, plus tool decisions, fact-check flags, time saved by the cache, guardrail decisions and JSON retries
- With no log yet, `/stats` prints `(no calls logged yet)`
- The web UI shows how full the context window is after each answer

**Audit every answer**

As a developer debugging the assistant, I want every answer and every decision recorded in a structured log, so that I can see exactly what happened and when.

Acceptance criteria:
- Each answer appends one JSON line to `calls.jsonl` with the time, model, prompt version, tokens and durations; tool calls, guardrail decisions, cache lookups and checks get their own lines
- A failed answer is also logged, with its error message
- The log path can be changed with `-l`; if the file can't be written, the chat continues with a warning
- Pending (#84): the prompt and the response logged (masked), and a web view of latency, errors and tokens over time and per prompt version, where any interaction can be opened

## [X3] Export & publication

Code: [export/](../export/) · How it works: [features.md](features.md#x3-export-and-share)

**Export a session**

As a developer, I want to export a conversation to Markdown, JSON or PDF, so that I can attach it to a report or a ticket.

Acceptance criteria:
- `python3 sensai.py --export <id> --format markdown|json|pdf` (or Export in the web UI) writes the chat to a file and prints its path
- The export keeps who said what, and personal data stays masked, as in the saved session
- An unknown session id gives `session '<id>' not found`

**Share a conversation with a link**

As a developer, I want a link to a read-only copy of a conversation, so that I can show it to a teammate without sending files.

Acceptance criteria:
- `python3 sensai.py --publish <id>` (or Share in the web UI) creates a frozen copy served by the web UI at `/s/<token>`
- The shared page can't be edited and leaves out the system prompt and the tool calls; personal data is masked
- A wrong or unknown token answers `share not found`, never another chat

## [X1] Web UI

Code: [web/server.py](../web/server.py), [web/static/](../web/static/) · How it works: [features.md](features.md#x1-web-ui)

**Use the assistant in the browser**

As a developer reading long answers with code, I want the same assistant in a browser, so that I can read and copy answers more comfortably than in a terminal.

Acceptance criteria:
- `python3 app.py` serves the chat at `http://localhost:8000`, with streaming answers, code blocks with a copy button, and each answer's tokens and speed
- Slash commands such as `/json`, `/persona` and `/guardrails` work as in the terminal, with a menu when typing `/`, because both use the same command router
- An error (Ollama down, a model without tools) is shown in the chat instead of breaking the page

**Manage everything without the terminal**

As a developer, I want to manage chats and settings from the page, so that I don't need to restart with different options.

Acceptance criteria:
- The sidebar lists the chats, resumes, renames or deletes one, or starts a new one; ☰ hides it to give the chat more room
- Settings changes the model, auto-approve, the semantic cache and the guardrail rules, applied to the next message
- Panels show and manage the knowledge bases, tools, file access, profile, personas, stats, saved answers, JSON schemas and prompt versions
