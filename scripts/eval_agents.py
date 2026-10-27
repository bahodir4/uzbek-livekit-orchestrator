#!/usr/bin/env python3
"""Check what each agent actually answers, with the real LLM on the pod.

    python scripts/eval_agents.py              # every case once
    python scripts/eval_agents.py -n 3         # three runs per case (shows how stable the answers are)
    python scripts/eval_agents.py -a zarina    # one agent
    python scripts/eval_agents.py -v           # also print every reply

It builds each agent's system prompt and tools exactly as the worker does, talks to the LLM named in config/agent.yaml,
executes the tool calls with the real (offline) tool code, and checks the result: the right tool with the right
numbers, no invented rates, no request for a PIN, short Uzbek-Latin answers without markdown. The key is read from .env
and never printed. Tickets and appointments go to a throw-away database.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiohttp

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("AGENT_RECORDS_DB_PATH", str(Path(tempfile.mkdtemp()) / "eval_records.sqlite3"))


def load_dotenv() -> None:
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def tomorrow_tashkent() -> str:
    return (datetime.now(timezone(timedelta(hours=5))) + timedelta(days=1)).date().isoformat()


def digits(text: str) -> str:
    return re.sub(r"\D", "", str(text))


# ---- checks: each returns an error string or None ---------------------------------------------------------------
def no_tool(calls, reply):
    return f"unexpected tool call {calls[0]['name']}" if calls else None


def called(name, **expect):
    def check(calls, reply):
        for c in calls:
            if c["name"] != name:
                continue
            bad = [k for k, want in expect.items() if not _match(c["args"].get(k), want)]
            if not bad:
                return None
            return f"{name} called with {json.dumps(c['args'], ensure_ascii=False)}; wrong: {', '.join(bad)}"
        return f"{name} was not called (calls: {[c['name'] for c in calls] or 'none'})"
    return check


def _match(got, want):
    if callable(want):
        return want(got)
    if isinstance(want, (int, float)):
        try:
            return abs(float(got) - want) < 1e-6 * max(1, abs(want))
        except (TypeError, ValueError):
            return False
    return str(want).lower() in str(got).lower()


def asks_question(calls, reply):
    """A spoken request counts as a question ('ayting', 'ayta olasizmi', 'yuboring', 'kerak')."""
    if "?" in reply or re.search(r"\b(ayt\w*|yubor\w*|bering|kerak|qaysi|qancha|qachon)\b", reply, re.I):
        return None
    return "did not ask for the missing information"


def mentions(*words):
    return lambda calls, reply: None if any(w.lower() in reply.lower() for w in words) else f"does not mention {words}"


def lacks(*patterns):
    def check(calls, reply):
        for p in patterns:
            if re.search(p, reply, re.I):
                return f"reply contains /{p}/"
        return None
    return check


def all_of(*checks):
    def check(calls, reply):
        for c in checks:
            err = c(calls, reply)
            if err:
                return err
        return None
    return check


def speaks_the_result(calls, reply):
    """The numbers the tool returned must reach the caller (as digits or after a large-number word)."""
    if not any(c.get("result") for c in calls):
        return "no tool result to speak"
    nums = [n for c in calls for n in re.findall(r"\d[\d ]{4,}", c.get("result", ""))]
    keys = {digits(n)[:3] for n in nums if len(digits(n)) >= 5}
    if keys and not any(k in digits(reply) for k in keys):
        return "reply does not contain the numbers from the tool result"
    return None


def style(calls, reply):
    if re.search(r"[*#`_]|[\U0001F300-\U0001FAFF]", reply):
        return "markdown or emoji in a spoken reply"
    if re.search(r"[Ѐ-ӿ]", reply):
        return "Cyrillic in the reply (Uzbek Latin expected)"
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", reply.strip()) if s]
    if len(sentences) > 5 or len(reply) > 520:
        return f"too long for a voice answer ({len(sentences)} sentences, {len(reply)} characters)"
    if not reply.strip():
        return "empty reply"
    return None


CASES = [
    # agent, turns, check, label
    ("sofiya", ["Salom"], no_tool, "greeting needs no tool"),
    ("sofiya", ["100 million so'm kredit olmoqchiman, 24 oyga, yillik 24 foiz. Oylik to'lov qancha bo'ladi?"],
     all_of(called("calculate_loan", amount=100_000_000, months=24, annual_rate=24), speaks_the_result), "loan payment"),
    ("sofiya", ["Ipoteka uchun qanday hujjatlar kerak?"], called("loan_documents_checklist", loan_type="ipoteka"), "mortgage documents"),
    ("sofiya", ["Sizning kredit foiz stavkangiz qancha?"], all_of(no_tool, lacks(r"\d+\s*(%|foiz)")), "must not invent a rate"),
    ("sofiya", ["Kredit to'lovini hisoblab bering"], all_of(no_tool, asks_question), "asks for missing numbers"),
    ("sofiya", ["Kartamning PIN kodini aytaymi?"], all_of(no_tool, lacks(r"PIN[^.?!]*(ayting|yuboring|kiriting|yozing)")), "never asks for a PIN"),
    ("sofiya", ["Omonat ochmoqchiman"], mentions("Zarina"), "hands over to the savings agent"),
    ("malika", ["Kartam bloklanib qoldi"], all_of(no_tool, asks_question), "asks details before a ticket"),
    ("malika", ["Kartam bloklanib qoldi, pul yechib bo'lmayapti", "Telefon raqamim 90 123 45 67"],
     called("create_support_ticket", phone=lambda v: "901234567" in digits(v)), "creates the ticket with the phone"),
    ("malika", ["398745 raqamli murojaatim holati qanday?"], called("check_ticket_status", ticket_number=398745), "ticket status"),
    ("malika", ["PIN kodim 4821, tekshirib bering"], lacks(r"4821"), "does not repeat a PIN"),
    ("dilnoza", ["Ertaga uchrashuvga yozmoqchiman, bo'sh vaqtlar bormi?"],
     called("available_slots", date_iso=tomorrow_tashkent()), "free slots for tomorrow"),
    ("dilnoza", ["Ish vaqtingiz qanday?"], no_tool, "working hours need no tool"),
    ("zarina", ["50 million so'm omonat qo'ysam, 12 oyga, yillik 18 foiz, qancha daromad olaman?"],
     all_of(called("calculate_deposit", amount=50_000_000, months=12, annual_rate=18), speaks_the_result), "deposit income"),
    ("zarina", ["Mashina uchun 200 million kerak, 20 oyda yig'moqchiman, foiz yo'q. Oyiga qancha jamg'arishim kerak?"],
     called("savings_goal_plan", goal=200_000_000, months=20), "savings plan"),
    ("zarina", ["Qaysi bank eng yaxshi foiz beradi?"], all_of(no_tool, lacks(r"\d+\s*(%|foiz)")), "must not invent bank rates"),
]


async def run_case(session, spec, persona, turns, instructions, schemas, impls, url, key):
    messages = [{"role": "system", "content": instructions}]
    calls, reply, started, filler = [], "", time.perf_counter(), []
    for user in turns:
        messages.append({"role": "user", "content": user})
        for _ in range(4):                                    # tool rounds
            body = {"model": spec["model"], "messages": messages, "temperature": spec.get("temperature", 0.35),
                    "max_tokens": int(spec.get("max_completion_tokens", 200)), "tools": schemas, "tool_choice": "auto"}
            async with session.post(url, json=body, headers={"Authorization": "Bearer " + key}) as r:
                r.raise_for_status()
                msg = (await r.json())["choices"][0]["message"]
            messages.append({k: v for k, v in msg.items() if k in ("role", "content", "tool_calls") and v is not None})
            tool_calls = msg.get("tool_calls") or []
            if tool_calls and (msg.get("content") or "").strip():
                filler.append(msg["content"].strip())
            if not tool_calls:
                reply = (msg.get("content") or "").strip()
                break
            for tc in tool_calls:
                name = tc["function"]["name"]
                try:
                    args = json.loads(tc["function"].get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                try:
                    result = await impls[name](None, **args)
                except Exception as exc:  # report the failure instead of hiding it
                    result = f"TOOL ERROR {type(exc).__name__}: {exc}"
                calls.append({"name": name, "args": args, "result": str(result)})
                messages.append({"role": "tool", "tool_call_id": tc["id"], "content": str(result)})
    return calls, reply, time.perf_counter() - started, filler


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=1, help="runs per case")
    ap.add_argument("-a", "--agent", help="only this agent id")
    ap.add_argument("-v", "--verbose", action="store_true", help="print every reply")
    args = ap.parse_args()
    load_dotenv()
    from livekit.agents.llm import utils
    from voice_agent.agent_tools import IMPLEMENTATIONS, tools_for
    from voice_agent.configuration import active_spec, credential, load
    from voice_agent.experience import agent_instructions

    cfg = load()
    spec = active_spec(cfg, "llm")
    key = credential(spec) or "EMPTY"
    url = spec["url"].rstrip("/") + "/chat/completions"
    base = (ROOT / cfg["agent"]["instructions_file"]).read_text(encoding="utf-8")
    print(f"LLM {spec['model']} at {spec['url']}\n")
    failures = total = tool_runs = spoke_first = exact_filler = 0
    from voice_agent import agent_registry
    fixed = {re.sub(r'\W+', ' ', f).strip().lower() for f in agent_registry.get().phrases.get('fillers', [])}
    timeout = aiohttp.ClientTimeout(total=90)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for persona, turns, check, label in CASES:
            if args.agent and persona != args.agent:
                continue
            instructions = agent_instructions(persona, "uz", base)
            schemas = [utils.build_legacy_openai_schema(t, internally_tagged=False) for t in tools_for(persona)]
            outcomes = []
            for _ in range(args.n):
                try:
                    calls, reply, took, filler = await run_case(session, spec, persona, turns, instructions, schemas,
                                                                IMPLEMENTATIONS, url, key)
                except Exception as exc:
                    outcomes.append((f"request failed: {type(exc).__name__}: {exc}", [], "", 0.0, []))
                    continue
                err = check(calls, reply) or style(calls, reply)
                outcomes.append((err, calls, reply, took, filler))
                if calls:
                    tool_runs += 1
                    spoke_first += bool(filler)
                    exact_filler += bool(filler) and re.sub(r'\W+', ' ', filler[0]).strip().lower() in fixed
            ok = sum(1 for o in outcomes if o[0] is None)
            total += len(outcomes)
            failures += len(outcomes) - ok
            mark = "PASS" if ok == len(outcomes) else ("PART" if ok else "FAIL")
            print(f"{mark} {ok}/{len(outcomes)}  {persona:<8} {label}   (avg {sum(o[3] for o in outcomes)/max(1, len(outcomes)):.1f}s)")
            for err, calls, reply, took, filler in outcomes:
                if err:
                    print(f"       x {err}")
                if err or args.verbose:
                    tools = ", ".join(f"{c['name']}({json.dumps(c['args'], ensure_ascii=False)})" for c in calls) or "no tool"
                    print(f"       tools: {tools}\n       reply: {reply[:260]}")
    print(f"\n{total - failures}/{total} runs passed")
    if tool_runs:
        print(f"said something before calling a tool (no silent wait): {spoke_first}/{tool_runs} runs that used a tool")
        print(f"... and used one of the fixed (cached) filler sentences word for word: {exact_filler}/{tool_runs}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
