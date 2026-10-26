# Agents: prompts, tools and business rules live in `agents/`

Four female Uzbek-speaking agents. Nothing about them is typed into Python: edit a file, restart the worker.

| File | What it holds |
|---|---|
| `agents/index.yaml` | agent order, default agent, ids from older UI builds (`legacy_aliases`), locale line |
| `agents/<id>.yaml` | one agent: `name`, `role`, `voice_gender`, `greeting`, `tasks`, `tools`, `prompt` |
| `agents/shared_rules.md` | rules appended to every agent prompt (`{directory}` lists all agents for hand-offs) |
| `agents/tools.yaml` | what the model is told about each tool (when to call it, what the arguments mean) |
| `agents/messages.yaml` | everything a tool says back, plus validation and error messages |
| `agents/settings.yaml` | business rules: working hours, slot length, capacity, limits, ticket categories, loan document lists |

Placeholders in prompts, greetings and tool descriptions: `{name}`, `{role}`, `{work_hours}` (built from `settings.yaml`).

| Agent | Task | Tools |
|---|---|---|
| Sofiya | Loan advisor | `calculate_loan`, `loan_documents_checklist` |
| Malika | Customer support | `create_support_ticket`, `check_ticket_status` |
| Dilnoza | Appointments and queue | `available_slots`, `book_appointment`, `cancel_appointment` |
| Zarina | Deposits and savings | `calculate_deposit`, `savings_goal_plan` |

All agents also get `current_date_time`. Tools are offline and deterministic (no external service). Tickets and
appointments are stored in `AGENT_RECORDS_DB_PATH` (the `conversation_data` volume).

## Add or change an agent
1. Add `agents/<id>.yaml` and list the id in `agents/index.yaml`.
2. Every tool in its `tools:` list needs a description in `agents/tools.yaml` and code in `voice_agent/agent_tools.py`
   (`IMPLEMENTATIONS`). New tools also need their texts in `messages.yaml`.
3. Mirror the id, name and role in the UI repo (`src/agents.json`); the UI warns in the console if the lists differ.
4. `python -m pytest tests/test_agent_registry.py` checks the files (placeholders, tool names, message keys).
   A broken file stops startup with a message naming the file and key.

The folder is mounted read-only into both containers: after editing, `docker compose restart worker api`.

## Voice gender
Prompts and names are female. The *sound* is decided by the TTS speaker reference (`NAVOIY_REFERENCE` on the TTS pod);
the gateway has one voice per model. Listen to a call and replace the reference audio if it does not sound female.
