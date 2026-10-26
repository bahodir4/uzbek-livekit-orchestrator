"""Tool logic for the specialised agents. Offline and deterministic: nothing here calls another service, so a tool
cannot fail because some other system is down.

Only logic lives here. What the model is told about each tool (agents/tools.yaml), what a tool says back
(agents/messages.yaml), the business rules (agents/settings.yaml) and which agent gets which tool
(agents/<id>.yaml) are files. Every wrapper returns text for the model to speak; errors become a polite message and
never raise into the call.
"""
from __future__ import annotations

import asyncio
import functools
import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Awaitable, Callable

from livekit.agents import RunContext, function_tool

from . import agent_registry, records

log = logging.getLogger(__name__)


def _reg() -> agent_registry.Registry:
    return agent_registry.get()


def text(path: str, **values) -> str:
    return _reg().text(path, **values)


def _cfg(name: str) -> dict:
    return _reg().settings[name]


def _now() -> datetime:
    return datetime.now(timezone(timedelta(hours=_reg().settings["timezone_offset_hours"])))


def _today() -> date:
    return _now().date()


def _minutes(hhmm: str) -> int:
    hours, minutes = hhmm.split(":")
    return int(hours) * 60 + int(minutes)


def slot_times() -> list[str]:
    cfg = _cfg("appointments")
    step, end = int(cfg["slot_minutes"]), _minutes(cfg["day_end"])
    breaks = [(_minutes(a), _minutes(b)) for a, b in cfg.get("breaks", [])]
    slots, t = [], _minutes(cfg["day_start"])
    while t + step <= end:
        if not any(a <= t < b for a, b in breaks):
            slots.append(f"{t // 60:02d}:{t % 60:02d}")
        t += step
    return slots


def _money(value: float) -> str:
    return text("money", value=round(value))  # plain digits: the Uzbek TTS normaliser expands them


def _check(value: float, field: str, low: float, high: float) -> float:
    if not isinstance(value, (int, float)) or value != value or not (low <= value <= high):
        raise ValueError(text("validation.range", name=text(f"fields.{field}"), low=low, high=high))
    return float(value)


def _phone(raw: str) -> str:
    digits = re.sub(r"\D", "", raw or "")
    if not 9 <= len(digits) <= 13:
        raise ValueError(text("validation.phone"))
    return digits


# ---- pure logic (unit tested) -------------------------------------------------------------

def loan_quote(amount: float, months: int, annual_rate: float, kind: str = "annuitet") -> dict:
    lim = _cfg("limits")
    amount = _check(amount, "loan_amount", 1, lim["amount_max"])
    months = int(_check(months, "months", 1, lim["loan_months_max"]))
    annual_rate = _check(annual_rate, "rate", 0, lim["rate_max"])
    i = annual_rate / 12 / 100
    if kind.startswith("diff"):
        first = amount / months + amount * i
        last = amount / months + (amount / months) * i
        total = amount + amount * i * (months + 1) / 2
        return {"kind": "differensial", "first": first, "last": last, "total": total, "overpay": total - amount}
    payment = amount / months if i == 0 else amount * i / (1 - (1 + i) ** -months)
    total = payment * months
    return {"kind": "annuitet", "monthly": payment, "total": total, "overpay": total - amount}


def deposit_quote(amount: float, months: int, annual_rate: float, monthly_capitalization: bool = True) -> dict:
    lim = _cfg("limits")
    amount = _check(amount, "deposit_amount", 1, lim["amount_max"])
    months = int(_check(months, "months", 1, lim["deposit_months_max"]))
    annual_rate = _check(annual_rate, "rate", 0, lim["rate_max"])
    i = annual_rate / 12 / 100
    final = amount * (1 + i) ** months if monthly_capitalization else amount * (1 + i * months)
    return {"final": final, "income": final - amount}


def savings_plan(goal: float, months: int, annual_rate: float = 0.0, initial: float = 0.0) -> dict:
    lim = _cfg("limits")
    goal = _check(goal, "goal", 1, lim["amount_max"])
    months = int(_check(months, "months", 1, lim["deposit_months_max"]))
    annual_rate = _check(annual_rate, "rate", 0, lim["rate_max"])
    initial = _check(initial, "initial", 0, lim["amount_max"])
    i = annual_rate / 12 / 100
    grown = initial * (1 + i) ** months
    if grown >= goal:
        return {"monthly": 0.0, "reached": True}
    need = (goal - grown) / months if i == 0 else (goal - grown) * i / ((1 + i) ** months - 1)
    return {"monthly": need, "reached": False}


def parse_day(raw: str) -> date:
    cfg = _cfg("appointments")
    try:
        day = date.fromisoformat((raw or "").strip())
    except ValueError:
        raise ValueError(text("validation.date_format")) from None
    today = _today()
    if day < today:
        raise ValueError(text("validation.date_past"))
    if day > today + timedelta(days=int(cfg["max_days_ahead"])):
        raise ValueError(text("validation.date_far", days=cfg["max_days_ahead"]))
    if day.weekday() not in cfg["work_days"]:
        days = ", ".join(_reg().messages["weekdays"][d] for d in sorted(cfg["work_days"]))
        raise ValueError(text("validation.date_closed", days=days))
    return day


def free_slots(day: date, taken: dict[str, int]) -> list[str]:
    capacity = int(_cfg("appointments")["capacity_per_slot"])
    now = _now()
    slots = [s for s in slot_times() if taken.get(s, 0) < capacity]
    if day == now.date():
        slots = [s for s in slots if s > now.strftime("%H:%M")]
    return slots


# ---- tool implementations (descriptions come from agents/tools.yaml) ------------------------

def _safe(fn: Callable[..., Awaitable[str]]):
    @functools.wraps(fn)
    async def inner(*args, **kwargs) -> str:
        try:
            return await fn(*args, **kwargs)
        except ValueError as exc:
            return text("errors.invalid", reason=exc)
        except records.RecordsError:
            log.exception("agent records storage failed in %s", fn.__name__)
            return text("errors.storage")
        except Exception:
            log.exception("agent tool %s failed", fn.__name__)
            return text("errors.generic")
    return inner


@_safe
async def calculate_loan(context: RunContext, amount: float, months: int, annual_rate: float,
                         kind: str = "annuitet") -> str:
    q = loan_quote(amount, months, annual_rate, kind)
    if q["kind"] == "annuitet":
        return text("results.loan_annuity", monthly=_money(q["monthly"]), total=_money(q["total"]),
                    overpay=_money(q["overpay"]))
    return text("results.loan_differential", first=_money(q["first"]), last=_money(q["last"]),
                total=_money(q["total"]), overpay=_money(q["overpay"]))


@_safe
async def loan_documents_checklist(context: RunContext, loan_type: str) -> str:
    docs = _cfg("loan_documents")
    key = next((k for k in docs if k in (loan_type or "").lower()), None)
    if key is None:
        raise ValueError(text("validation.loan_type", kinds=", ".join(docs)))
    return text("results.loan_documents", kind=key.capitalize(), items=", ".join(docs[key]))


@_safe
async def create_support_ticket(context: RunContext, category: str, summary: str, phone: str) -> str:
    cfg = _cfg("tickets")
    cat = (category or "").lower().strip()
    cat = cat if cat in cfg["categories"] else cfg["default_category"]
    if not (summary or "").strip():
        raise ValueError(text("validation.summary_empty"))
    number = await asyncio.to_thread(records.create_ticket, cat, summary.strip(), _phone(phone))
    return text("results.ticket_created", number=number)


@_safe
async def check_ticket_status(context: RunContext, ticket_number: int) -> str:
    ticket = await asyncio.to_thread(records.get_ticket, int(ticket_number))
    if ticket is None:
        return text("results.ticket_missing")
    return text("results.ticket_status", number=ticket["id"], category=ticket["category"], status=ticket["status"])


@_safe
async def available_slots(context: RunContext, date_iso: str) -> str:
    day = parse_day(date_iso)
    taken = await asyncio.to_thread(records.booked_slots, day.isoformat())
    slots = free_slots(day, taken)
    if not slots:
        return text("results.slots_none", day=day.isoformat())
    more = text("results.slots_more", count=len(slots) - 6) if len(slots) > 6 else ""
    return text("results.slots_free", day=day.isoformat(), times=", ".join(slots[:6]), more=more)


@_safe
async def book_appointment(context: RunContext, date_iso: str, time_hhmm: str, full_name: str, phone: str,
                           purpose: str) -> str:
    cfg = _cfg("appointments")
    day = parse_day(date_iso)
    slot = (time_hhmm or "").strip()
    if slot not in slot_times():
        raise ValueError(text("validation.slot_invalid", work_hours=agent_registry.work_hours_text(
            _reg().settings, _reg().messages)))
    if day == _today() and slot <= _now().strftime("%H:%M"):
        raise ValueError(text("validation.slot_passed"))
    if not (full_name or "").strip():
        raise ValueError(text("validation.name_empty"))
    number = await asyncio.to_thread(
        records.create_appointment, day.isoformat(), slot, full_name.strip(), _phone(phone),
        (purpose or "").strip() or text("validation.no_purpose"), int(cfg["capacity_per_slot"]))
    if number is None:
        return text("results.appointment_taken")
    return text("results.appointment_confirmed", day=day.isoformat(), slot=slot, number=number)


@_safe
async def cancel_appointment(context: RunContext, appointment_number: int, phone: str) -> str:
    done = await asyncio.to_thread(records.cancel_appointment, int(appointment_number), _phone(phone))
    return text("results.appointment_cancelled" if done else "results.appointment_not_found")


@_safe
async def calculate_deposit(context: RunContext, amount: float, months: int, annual_rate: float,
                            monthly_capitalization: bool = True) -> str:
    q = deposit_quote(amount, months, annual_rate, monthly_capitalization)
    return text("results.deposit", final=_money(q["final"]), income=_money(q["income"]))


@_safe
async def savings_goal_plan(context: RunContext, goal: float, months: int, annual_rate: float = 0.0,
                            initial: float = 0.0) -> str:
    q = savings_plan(goal, months, annual_rate, initial)
    if q["reached"]:
        return text("results.savings_reached")
    return text("results.savings_need", monthly=_money(q["monthly"]))


@_safe
async def current_date_time(context: RunContext) -> str:
    now = _now()
    return text("results.current_time", now=now.strftime("%Y-%m-%d %H:%M"),
                weekday=_reg().messages["weekdays"][now.weekday()])


IMPLEMENTATIONS = {fn.__name__: fn for fn in (
    calculate_loan, loan_documents_checklist, create_support_ticket, check_ticket_status, available_slots,
    book_appointment, cancel_appointment, calculate_deposit, savings_goal_plan, current_date_time)}


def tools_for(persona_id: str) -> list:
    """LiveKit tools for one agent: the names listed in agents/<id>.yaml, described by agents/tools.yaml."""
    reg = _reg()
    spec = reg.agents.get(persona_id)
    tools = []
    for name in (spec.tools if spec else ()):
        impl = IMPLEMENTATIONS.get(name)
        if impl is None:
            log.error("agents/%s.yaml lists tool '%s' that has no implementation", persona_id, name)
            continue
        tools.append(function_tool(impl, name=name, description=reg.tool_descriptions[name]))
    return tools
