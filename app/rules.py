"""Sorting rules: "title contains X" files an Inbox task under a class."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import SortRule, Task


def load_rules(db: Session) -> list[SortRule]:
    # Longer keywords first, so "AP Bio" wins over "Bio"
    rules = db.scalars(select(SortRule)).all()
    return sorted(rules, key=lambda r: (-len(r.keyword), r.id))


def match(rules: list[SortRule], title: str) -> int | None:
    t = title.lower()
    for r in rules:
        if r.keyword.lower() in t:
            return r.project_id
    return None


def apply_to_inbox(db: Session) -> int:
    rules = load_rules(db)
    if not rules:
        return 0
    moved = 0
    for task in db.scalars(select(Task).where(Task.project_id.is_(None), Task.done.is_(False))):
        pid = match(rules, task.title)
        if pid is not None:
            task.project_id = pid
            moved += 1
    return moved
