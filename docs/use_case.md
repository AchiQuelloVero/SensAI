# Use case

## An assistant for a developer working alone on a long-term project

**Who.** A developer working alone on a project that lasts weeks or months: a freelancer maintaining a client's application, or a student on a semester-long project. They work on a laptop, sometimes offline, and often under an NDA or school rules about where code and data may go.

**What they are trying to do.** Get help with the project day after day: find answers in the project's own documentation, look up library docs and recent information, read and change project files, turn answers into data their scripts can use, try different approaches, and pick up each morning where they stopped the day before.

**Why they can't do it without Sensai.** A cloud assistant means sending the client's code and data to a third party, which the NDA or the school may forbid. It also forgets the project between conversations, and nothing stops it from changing files without supervision. Sensai runs on the laptop with Ollama, remembers sessions, answers from the project's own documents, hides personal data before anything reaches the model or the logs, and asks before any action that changes the project. A stronger cloud model through OpenRouter is optional: even then, the safety, privacy and cache checks run on the local model, so the raw message never leaves the laptop, only the checked one with personal data masked.

## A day with Sensai

The same day, step by step with real outputs, is the demo in [demo.md](demo.md).

1. **Morning.** The developer reopens yesterday's chat: the assistant remembers the bug they were chasing and the approaches already tried, and their profile already tells it their language and preferences. An old chat they no longer need is deleted.
2. **Project knowledge.** The client's specification is in `knowledge/`. With `/rag on`, "What does the spec say about password resets?" is answered from the spec, with `[spec.md, chunk 1]` citations; a question the spec doesn't cover gets "the spec does not mention it" instead of a guess.
3. **Up-to-date information.** A library changed its API last month. The assistant searches the web and reads the documentation through MCP servers, instead of relying on what it learned during training.
4. **Changing files, safely.** Asked to create a file in the project folder, the assistant shows the exact call and waits for approval before writing anything; folders marked off-limits are refused before any tool runs.
5. **Code that runs.** The assistant runs the code it writes in a Docker sandbox, sees the error when it fails and fixes it; an endless loop is stopped after 5 seconds.
6. **Client data stays private.** The developer pastes an error report that contains a customer's email and phone number: they are replaced with `[EMAIL]` and `[PHONE]` before reaching the model, and never written to the logs. An answer that would copy colleagues' salaries out of a file is withdrawn.
7. **Data for scripts.** `/json` turns a bug description into JSON that matches the tracker's schema; when a reply breaks the schema, the assistant is told what was wrong and fixes it.
8. **A strict review.** `/persona vera` switches to a strict code reviewer without losing the conversation; she refuses what is outside her scope.
9. **Asked again.** A question asked yesterday in other words is answered from the cache in about a second.
10. **Exploring.** Unsure between two designs, the developer edits an earlier message to try the other one, and switches between the two branches.
11. **Tuning.** The developer edits the system prompt, compares the old and new versions on the same question, and rolls back if needed.
12. **Checking.** Before relying on a factual answer, the fact-check lists the claims nothing supports.
13. **Sharing.** The conversation goes to the client as a PDF, or as a read-only link with personal data masked.
14. **Monitoring.** `/stats` shows what was called, how fast, what failed, and what the guardrails and the cache did.

## Features and their role

| Feature | Role in this use case |
|---|---|
| [B1] Chat | A local model that streams its answers and explains errors instead of crashing |
| [M1] Session persistence & profile | Continue the project across days without re-explaining it |
| [R1] Knowledge bases (RAG) | Answer from the project's own documents, with sources |
| [T1] Tool calling & MCP | Reach files, time, the web and other programs through standard tool servers |
| [T3] Web search | Information more recent than the model's training |
| [A3] Human-in-the-loop | Nothing changes in the project without the developer's approval |
| [T4] File access | The assistant works inside its workspace only, with rules per folder |
| [T2] Sandboxed code execution | Code is run and corrected, without access to the laptop |
| [EV2] Guardrails | Client personal data never reaches the model or the logs; harmful, off-topic and leaking answers are refused |
| [T5] Structured output | Answers as JSON that scripts and the other features can rely on |
| [A5] Personas | Switch between a general assistant, a code reviewer and a tutor in the same conversation |
| [A6] Semantic cache | Questions asked again, in other words, are answered instantly |
| [X2] Branching & interrupt | Compare approaches without losing the original; stop a reply that goes wrong |
| [A4] Prompt versioning | Change the assistant's instructions safely, compare and roll back |
| [EV1] Hallucination check | Claims nothing supports are flagged before the developer relies on them |
| [X3] Export & publication | Send the conversation to the client as a file or a read-only link |
| [EV3] Logging & monitoring | See what was called, how fast, and what failed |
| [X1] Web UI | The same assistant in the browser, with every setting in reach |
