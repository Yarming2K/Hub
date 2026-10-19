"""Import assignments from a Schoology calendar feed (the private iCal link in Schoology Settings)."""
import os
import re
from datetime import date, datetime, timedelta
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import httpx
from icalendar import Calendar
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Project, Setting, SyncItem, Task, now_utc

LOCAL_TZ = ZoneInfo(os.environ.get("HUB_TIMEZONE", "America/New_York"))
MAX_FEED_BYTES = 5 * 1024 * 1024
LOOKBACK_DAYS = 14  # skip assignments that were due more than this long ago

# Only fetch from Schoology itself, so the server can't be pointed at other sites.
# Districts with their own Schoology address can add it with SCHOOLOGY_HOSTS=lms.example.org
_DEFAULT_HOSTS = ("schoology.com", "fcps.edu")


def allowed_hosts() -> tuple[str, ...]:
    extra = tuple(h.strip().lower() for h in os.environ.get("SCHOOLOGY_HOSTS", "").split(",") if h.strip())
    return _DEFAULT_HOSTS + extra


class FeedError(ValueError):
    pass


def normalize_url(raw: str) -> str:
    url = raw.strip()
    if url.lower().startswith("webcal://"):
        url = "https://" + url[len("webcal://"):]
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise FeedError("That doesn't look like a calendar link. Copy the whole link from Schoology Settings.")
    host = parsed.hostname.lower()
    if not any(host == h or host.endswith("." + h) for h in allowed_hosts()):
        raise FeedError(f"Only Schoology calendar links are allowed (got {host}).")
    if "/calendar/" not in parsed.path:
        raise FeedError("That isn't a Schoology calendar link. Use the iCal link from Schoology Settings.")
    return url


# ---------- settings ----------

def get_setting(db: Session, key: str, default: str = "") -> str:
    s = db.get(Setting, key)
    return s.value if s else default


def set_setting(db: Session, key: str, value: str | None):
    s = db.get(Setting, key)
    if value is None:
        if s:
            db.delete(s)
        return
    if s:
        s.value = value
    else:
        db.add(Setting(key=key, value=value))


# ---------- parsing ----------

def _text(component, name: str) -> str:
    v = component.get(name)
    if v is None:
        return ""
    if isinstance(v, list):
        v = v[0]
    if hasattr(v, "cats"):  # CATEGORIES
        return ", ".join(str(c) for c in v.cats)
    return str(v).strip()


def _due_date(component) -> date | None:
    dt = component.get("DTSTART") or component.get("DUE")
    if dt is None:
        return None
    v = dt.dt
    if isinstance(v, datetime):
        if v.tzinfo is None:
            return v.date()
        return v.astimezone(LOCAL_TZ).date()
    return v


_COURSE_LINE = re.compile(r"^(?:course|section|class)\s*:\s*(.+)$", re.I | re.M)


def course_name(component) -> str:
    """Best guess at the class an event belongs to. Empty when the feed doesn't say."""
    for prop in ("CATEGORIES", "LOCATION", "X-SCHOOLOGY-COURSE"):
        name = _text(component, prop)
        if name:
            return name[:120]
    m = _COURSE_LINE.search(_text(component, "DESCRIPTION"))
    return m.group(1).strip()[:120] if m else ""


def _link(component) -> str | None:
    url = _text(component, "URL")
    if not url:
        m = re.search(r"https://\S+", _text(component, "DESCRIPTION"))
        url = m.group(0).rstrip(").,") if m else ""
    return url[:500] if url.startswith("https://") else None


def parse_feed(data: bytes) -> tuple[list[dict], list[str]]:
    try:
        cal = Calendar.from_ical(data)
    except Exception as exc:  # noqa: BLE001
        raise FeedError("Schoology sent something that isn't a calendar. Check the link.") from exc
    events, fields = [], set()
    for comp in cal.walk():
        if comp.name not in ("VEVENT", "VTODO"):
            continue
        fields.update(comp.keys())
        uid = _text(comp, "UID")
        title = _text(comp, "SUMMARY")
        if not uid or not title:
            continue
        events.append({
            "uid": uid[:300],
            "title": title[:300],
            "due": _due_date(comp),
            "course": course_name(comp),
            "link": _link(comp),
        })
    return events, sorted(fields)


# ---------- syncing ----------

def fetch_feed(url: str) -> bytes:
    try:
        with httpx.Client(timeout=20, follow_redirects=True, headers={"User-Agent": "Hub/0.2 calendar sync"}) as c:
            with c.stream("GET", url) as r:
                if r.status_code in (401, 403, 404):
                    raise FeedError("Schoology turned down the link. It may have been reset. Copy a fresh one from Settings.")
                r.raise_for_status()
                # every redirect must stay on an allowed host
                normalize_url(str(r.url))
                chunks, size = [], 0
                for chunk in r.iter_bytes():
                    size += len(chunk)
                    if size > MAX_FEED_BYTES:
                        raise FeedError("The calendar feed is too large to import.")
                    chunks.append(chunk)
                return b"".join(chunks)
    except httpx.HTTPError as exc:
        raise FeedError("Couldn't reach Schoology. Try again in a minute.") from exc


def apply_events(db: Session, events: list[dict], today: date | None = None) -> dict:
    today = today or datetime.now(LOCAL_TZ).date()
    cutoff = today - timedelta(days=LOOKBACK_DAYS)
    projects = {p.name.lower(): p for p in db.scalars(select(Project))}
    known = {s.uid: s for s in db.scalars(select(SyncItem))}
    seen: set[str] = set()
    added = updated = skipped = 0
    new_projects: list[str] = []

    for ev in events:
        if ev["uid"] in seen:  # duplicate UID within one feed
            continue
        seen.add(ev["uid"])
        item = known.get(ev["uid"])
        if item is not None:
            task = db.get(Task, item.task_id) if item.task_id else None
            if task is None:  # you deleted it, so leave it deleted
                skipped += 1
                continue
            changed = False
            for field in ("title", "due", "link"):
                if ev[field] is not None and getattr(task, field) != ev[field]:
                    setattr(task, field, ev[field])
                    changed = True
            updated += changed
            continue

        if ev["due"] is not None and ev["due"] < cutoff:
            continue  # old history; don't flood the list

        project = None
        if ev["course"]:
            project = projects.get(ev["course"].lower())
            if project is None:
                project = Project(name=ev["course"], color="#d4a017", next_step="")
                db.add(project)
                db.flush()
                projects[project.name.lower()] = project
                new_projects.append(project.name)

        task = Task(title=ev["title"], due=ev["due"], link=ev["link"], source="schoology",
                    project_id=project.id if project else None)
        db.add(task)
        db.flush()
        db.add(SyncItem(uid=ev["uid"], task_id=task.id))
        added += 1

    return {"added": added, "updated": updated, "skipped_deleted": skipped, "new_projects": new_projects}


def sync(db: Session) -> dict:
    url = get_setting(db, "schoology_url")
    if not url:
        raise FeedError("Schoology isn't connected yet.")
    events, fields = parse_feed(fetch_feed(url))
    result = apply_events(db, events)
    result.update({
        "events_in_feed": len(events),
        "with_course": sum(1 for e in events if e["course"]),
        "fields_seen": fields,
        "synced_at": now_utc().isoformat(),
    })
    set_setting(db, "schoology_last_sync", result["synced_at"])
    db.commit()
    return result
