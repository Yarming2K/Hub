"""Study coach: a short-answer tutor for one assignment, using the Anthropic Messages API."""
import base64
import os
from datetime import datetime

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Attachment, CoachMessage, Setting, Task
from .schoology import LOCAL_TZ

API_URL = "https://api.anthropic.com/v1/messages"
MODEL = os.environ.get("COACH_MODEL", "claude-haiku-5-5")
DAILY_LIMIT = int(os.environ.get("COACH_DAILY_LIMIT", "80"))
HISTORY = 20  # messages sent back to the model each turn
MAX_FILES = 4
MAX_FILE_BYTES = 8 * 1024 * 1024
IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}

SYSTEM = """You are the study coach inside a student's planner app. You are helping with one assignment.

Assignment: {title}
Class: {klass}
Due: {due}
Today: {today}

How to help:
- Help them understand, plan and practice: break the work into small steps, explain ideas with your own examples, ask guiding questions, quiz them, and give feedback on work they share.
- Don't produce work meant to be handed in as-is: no finished essays or paragraphs, no worksheet answers, no full solutions to the assigned problems. If they ask for that, say in one line that you'll help them get there themselves, then give the next best help (questions to outline with, a hint, a worked example on a similar problem with different numbers, or feedback on their draft).
- Keep every reply short: two or three sentences, or up to five short bullets. One step at a time. Plain words. Bold only the key term. No headings.
- If worksheet photos or PDFs are attached, use them to understand the task. Say so if a photo is too blurry to read.
- If they seem behind or stressed, help them pick the single next thing to do. Be encouraging and honest."""


class CoachError(Exception):
    pass


def api_key() -> str | None:
    return os.environ.get("ANTHROPIC_API_KEY") or None


def enabled() -> bool:
    return bool(api_key())


def _count_today(db: Session, bump: bool) -> int:
    key = "coach_count_" + datetime.now(LOCAL_TZ).strftime("%Y-%m-%d")
    row = db.get(Setting, key)
    n = int(row.value) if row else 0
    if bump:
        if row:
            row.value = str(n + 1)
        else:
            db.add(Setting(key=key, value="1"))
    return n


def _file_blocks(db: Session, task: Task) -> tuple[list[dict], list[str]]:
    blocks, names, total = [], [], 0
    files = db.scalars(select(Attachment).where(Attachment.task_id == task.id).order_by(Attachment.id)).all()
    for a in files[:MAX_FILES]:
        if total + a.size > MAX_FILE_BYTES:
            break
        data = base64.b64encode(a.data).decode()
        if a.content_type in IMAGE_TYPES:
            blocks.append({"type": "image", "source": {"type": "base64", "media_type": a.content_type, "data": data}})
        elif a.content_type == "application/pdf":
            blocks.append({"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": data}})
        else:
            continue
        names.append(a.filename)
        total += a.size
    return blocks, names


def build_request(db: Session, task: Task, history: list[CoachMessage], new_text: str) -> dict:
    today = datetime.now(LOCAL_TZ).date()
    system = SYSTEM.format(
        title=task.title,
        klass=task.project.name if task.project else "not set",
        due=task.due.strftime("%A %B %-d") if task.due else "no due date",
        today=today.strftime("%A %B %-d"),
    )
    if task.link:
        system += f"\nSchoology link (you can't open it): {task.link}"

    turns = [{"role": m.role, "content": m.content} for m in history[-HISTORY:]]
    # The window must start with the student, so drop a leading coach reply
    if turns and turns[0]["role"] == "assistant":
        turns = turns[1:]
    turns.append({"role": "user", "content": new_text})

    files, names = _file_blocks(db, task)
    if files:
        first = turns[0]
        note = {"type": "text", "text": "Attached for this assignment: " + ", ".join(names)}
        first["content"] = files + [note, {"type": "text", "text": first["content"]}]
    return {"model": MODEL, "max_tokens": 900, "system": system, "messages": turns}


def _call(payload: dict) -> str:
    key = api_key()
    if not key:
        raise CoachError("The coach isn't set up yet. Add ANTHROPIC_API_KEY in Render.")
    try:
        r = httpx.post(API_URL, json=payload, timeout=60, headers={
            "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    except httpx.HTTPError as exc:
        raise CoachError("Couldn't reach the coach. Try again in a moment.") from exc
    if r.status_code == 200:
        text = "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text").strip()
        if not text:
            raise CoachError("The coach didn't answer. Try asking another way.")
        return text
    try:
        msg = r.json().get("error", {}).get("message", "")
    except ValueError:
        msg = ""
    if r.status_code in (401, 403):
        raise CoachError("The API key was rejected. Check ANTHROPIC_API_KEY in Render.")
    if "credit balance" in msg.lower():
        raise CoachError("The Anthropic account is out of credit. Add credit at console.anthropic.com.")
    if r.status_code == 429:
        raise CoachError("Too many questions too fast. Wait a minute and try again.")
    if r.status_code in (500, 502, 503, 529):
        raise CoachError("The coach is busy right now. Try again in a minute.")
    print(f"[hub] coach error {r.status_code}: {msg[:200]}", flush=True)
    raise CoachError("The coach couldn't answer that. Try again, or remove a large attachment.")


def history(db: Session, task: Task) -> list[CoachMessage]:
    return list(db.scalars(select(CoachMessage).where(CoachMessage.task_id == task.id).order_by(CoachMessage.id)))


def ask(db: Session, task: Task, text: str) -> CoachMessage:
    if _count_today(db, bump=False) >= DAILY_LIMIT:
        raise CoachError(f"That's the daily limit of {DAILY_LIMIT} coach messages. It resets tomorrow.")
    reply = _call(build_request(db, task, history(db, task), text))
    # Only save the turn once the coach has answered, so a failure can simply be retried
    db.add(CoachMessage(task_id=task.id, role="user", content=text))
    msg = CoachMessage(task_id=task.id, role="assistant", content=reply)
    db.add(msg)
    _count_today(db, bump=True)
    db.commit()
    return msg
