import os
import secrets
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import AfterValidator, BaseModel, ConfigDict, Field
from typing import Annotated
from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from . import coach, schoology
from . import rules as sort_rules
from .db import get_db, migrate
from .models import Attachment, Project, SortRule, SyncItem, Task, now_utc

MAX_UPLOAD_BYTES = 10 * 1024 * 1024

# Detect the real file type from its first bytes instead of trusting the browser
_SIGNATURES = [
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"%PDF-", "application/pdf"),
]


def sniff_type(data: bytes) -> str | None:
    for sig, kind in _SIGNATURES:
        if data.startswith(sig):
            return kind
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

from sqlalchemy.engine import make_url as _make_url  # noqa: E402
from .db import DATABASE_URL as _DB_URL  # noqa: E402

_u = _make_url(_DB_URL)
print(f"[hub] connecting to {_u.get_backend_name()} database at {_u.host or 'local file'}", flush=True)
migrate()
print("[hub] database ready", flush=True)

app = FastAPI(title="Hub", version="0.1.0")


# ---------- auth ----------
# Set APP_TOKEN in production. Locally (unset) the API is open.

def require_token(authorization: str | None = Header(default=None)):
    expected = os.environ.get("APP_TOKEN")
    if not expected:
        return
    given = (authorization or "").removeprefix("Bearer ").strip()
    if not secrets.compare_digest(given, expected):
        raise HTTPException(status_code=401, detail="Bad or missing token")


# ---------- schemas ----------

# SQLite returns naive datetimes; they are stored as UTC, so mark them as such
UtcDatetime = Annotated[datetime, AfterValidator(lambda d: d if d.tzinfo else d.replace(tzinfo=timezone.utc))]

class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    color: str = "#5b8def"
    status: str = Field(default="active", pattern="^(active|paused|done)$")
    next_step: str = ""
    notes: str = ""


class ProjectPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    color: str | None = None
    status: str | None = Field(default=None, pattern="^(active|paused|done)$")
    next_step: str | None = None
    notes: str | None = None


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    color: str
    status: str
    next_step: str
    notes: str
    created_at: UtcDatetime
    open_tasks: int = 0


class TaskIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    project_id: int | None = None
    priority: int = Field(default=1, ge=0, le=2)
    due: date | None = None
    today: bool = False


class TaskPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    project_id: int | None = None
    priority: int | None = Field(default=None, ge=0, le=2)
    due: date | None = None
    today: bool | None = None
    done: bool | None = None


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    title: str
    project_id: int | None
    priority: int
    due: date | None
    today: bool
    done: bool
    created_at: UtcDatetime
    completed_at: UtcDatetime | None
    source: str | None = None
    link: str | None = None


class AttachmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    task_id: int | None
    project_id: int | None
    filename: str
    content_type: str
    size: int
    created_at: UtcDatetime


# ---------- helpers ----------

def _get_or_404(db: Session, model, obj_id: int):
    obj = db.get(model, obj_id)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"{model.__name__} not found")
    return obj


def _forget_tasks(db: Session, task_ids: list[int]):
    # Imported tasks you delete stay deleted: the sync record keeps the event ID but drops the task
    if task_ids:
        db.execute(update(SyncItem).where(SyncItem.task_id.in_(task_ids)).values(task_id=None))


def _project_out(p: Project) -> ProjectOut:
    out = ProjectOut.model_validate(p)
    out.open_tasks = sum(1 for t in p.tasks if not t.done)
    return out


# ---------- routes ----------

@app.get("/api/health")
def health():
    return {"ok": True, "auth": bool(os.environ.get("APP_TOKEN"))}


api = [Depends(require_token)]


@app.get("/api/projects", response_model=list[ProjectOut], dependencies=api)
def list_projects(db: Session = Depends(get_db)):
    projects = db.scalars(select(Project).order_by(Project.created_at)).all()
    return [_project_out(p) for p in projects]


@app.post("/api/projects", response_model=ProjectOut, status_code=201, dependencies=api)
def create_project(body: ProjectIn, db: Session = Depends(get_db)):
    p = Project(**body.model_dump())
    db.add(p)
    db.commit()
    db.refresh(p)
    return _project_out(p)


@app.patch("/api/projects/{project_id}", response_model=ProjectOut, dependencies=api)
def update_project(project_id: int, body: ProjectPatch, db: Session = Depends(get_db)):
    p = _get_or_404(db, Project, project_id)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(p, k, v)
    db.commit()
    db.refresh(p)
    return _project_out(p)


@app.delete("/api/projects/{project_id}", status_code=204, dependencies=api)
def delete_project(project_id: int, db: Session = Depends(get_db)):
    p = _get_or_404(db, Project, project_id)
    _forget_tasks(db, [t.id for t in p.tasks])
    db.delete(p)
    db.commit()


@app.get("/api/tasks", response_model=list[TaskOut], dependencies=api)
def list_tasks(
    project_id: int | None = None,
    done_days: int = Query(default=7, ge=0, le=365, description="Include tasks completed in the last N days"),
    db: Session = Depends(get_db),
):
    cutoff = now_utc() - timedelta(days=done_days)
    q = select(Task).where(or_(Task.done.is_(False), Task.completed_at >= cutoff))
    if project_id is not None:
        q = q.where(Task.project_id == project_id)
    q = q.order_by(Task.done, Task.priority.desc(), Task.due.is_(None), Task.due, Task.created_at)
    return db.scalars(q).all()


@app.post("/api/tasks", response_model=TaskOut, status_code=201, dependencies=api)
def create_task(body: TaskIn, db: Session = Depends(get_db)):
    if body.project_id is not None:
        _get_or_404(db, Project, body.project_id)
    t = Task(**body.model_dump())
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


@app.patch("/api/tasks/{task_id}", response_model=TaskOut, dependencies=api)
def update_task(task_id: int, body: TaskPatch, db: Session = Depends(get_db)):
    t = _get_or_404(db, Task, task_id)
    changes = body.model_dump(exclude_unset=True)
    if changes.get("project_id") is not None:
        _get_or_404(db, Project, changes["project_id"])
    if "done" in changes and changes["done"] != t.done:
        t.completed_at = now_utc() if changes["done"] else None
    for k, v in changes.items():
        setattr(t, k, v)
    db.commit()
    db.refresh(t)
    return t


@app.delete("/api/tasks/{task_id}", status_code=204, dependencies=api)
def delete_task(task_id: int, db: Session = Depends(get_db)):
    t = _get_or_404(db, Task, task_id)
    _forget_tasks(db, [t.id])
    db.delete(t)
    db.commit()


@app.get("/api/attachments", response_model=list[AttachmentOut], dependencies=api)
def list_attachments(
    task_id: int | None = None,
    project_id: int | None = None,
    db: Session = Depends(get_db),
):
    q = select(Attachment).order_by(Attachment.created_at)
    if task_id is not None:
        q = q.where(Attachment.task_id == task_id)
    if project_id is not None:
        q = q.where(Attachment.project_id == project_id)
    return db.scalars(q).all()


@app.post("/api/attachments", response_model=AttachmentOut, status_code=201, dependencies=api)
async def upload_attachment(
    file: UploadFile = File(...),
    task_id: int | None = Form(default=None),
    project_id: int | None = Form(default=None),
    db: Session = Depends(get_db),
):
    if (task_id is None) == (project_id is None):
        raise HTTPException(status_code=422, detail="Attach the file to either a task or a project")
    if task_id is not None:
        _get_or_404(db, Task, task_id)
    else:
        _get_or_404(db, Project, project_id)

    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File is over 10 MB. Try a smaller photo or PDF.")
    kind = sniff_type(data)
    if kind is None:
        raise HTTPException(status_code=415, detail="Only photos (PNG, JPG, GIF, WebP) and PDFs can be attached")

    name = Path(file.filename or "file").name[:200] or "file"
    a = Attachment(task_id=task_id, project_id=project_id, filename=name, content_type=kind, size=len(data), data=data)
    db.add(a)
    db.commit()
    db.refresh(a)
    return a


@app.get("/api/attachments/{attachment_id}/file", dependencies=api)
def download_attachment(attachment_id: int, db: Session = Depends(get_db)):
    a = _get_or_404(db, Attachment, attachment_id)
    safe_name = "".join(c if c.isalnum() or c in "._- " else "_" for c in a.filename)
    return Response(
        content=a.data,
        media_type=a.content_type,
        headers={
            "Content-Disposition": f'inline; filename="{safe_name}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, max-age=86400",
        },
    )


@app.delete("/api/attachments/{attachment_id}", status_code=204, dependencies=api)
def delete_attachment(attachment_id: int, db: Session = Depends(get_db)):
    db.delete(_get_or_404(db, Attachment, attachment_id))
    db.commit()


# ---------- Schoology ----------

class SchoologyConnect(BaseModel):
    url: str = Field(min_length=10, max_length=600)


def _schoology_status(db: Session) -> dict:
    return {
        "connected": bool(schoology.get_setting(db, "schoology_url")),
        "last_sync": schoology.get_setting(db, "schoology_last_sync") or None,
    }


@app.get("/api/schoology", dependencies=api)
def schoology_status(db: Session = Depends(get_db)):
    return _schoology_status(db)


@app.put("/api/schoology", dependencies=api)
def schoology_connect(body: SchoologyConnect, db: Session = Depends(get_db)):
    try:
        url = schoology.normalize_url(body.url)
    except schoology.FeedError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    schoology.set_setting(db, "schoology_url", url)
    schoology.set_setting(db, "schoology_last_sync", None)
    db.commit()
    return _schoology_status(db)


@app.delete("/api/schoology", status_code=204, dependencies=api)
def schoology_disconnect(db: Session = Depends(get_db)):
    # Imported tasks stay; only the link is forgotten
    schoology.set_setting(db, "schoology_url", None)
    schoology.set_setting(db, "schoology_last_sync", None)
    db.commit()


@app.post("/api/schoology/sync", dependencies=api)
def schoology_sync(db: Session = Depends(get_db)):
    try:
        return schoology.sync(db)
    except schoology.FeedError as exc:
        db.rollback()
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# ---------- bulk move and sorting rules ----------

class BulkMove(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=500)
    project_id: int | None = None


@app.post("/api/tasks/move", dependencies=api)
def move_tasks(body: BulkMove, db: Session = Depends(get_db)):
    if body.project_id is not None:
        _get_or_404(db, Project, body.project_id)
    tasks = db.scalars(select(Task).where(Task.id.in_(body.ids))).all()
    for t in tasks:
        t.project_id = body.project_id
    db.commit()
    return {"moved": len(tasks)}


class RuleIn(BaseModel):
    keyword: str = Field(min_length=1, max_length=60)
    project_id: int


class RuleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    keyword: str
    project_id: int


@app.get("/api/rules", response_model=list[RuleOut], dependencies=api)
def list_rules(db: Session = Depends(get_db)):
    return db.scalars(select(SortRule).order_by(SortRule.id)).all()


@app.post("/api/rules", response_model=RuleOut, status_code=201, dependencies=api)
def create_rule(body: RuleIn, db: Session = Depends(get_db)):
    _get_or_404(db, Project, body.project_id)
    keyword = body.keyword.strip()
    if not keyword:
        raise HTTPException(status_code=422, detail="Type a word to look for")
    r = SortRule(keyword=keyword, project_id=body.project_id)
    db.add(r)
    db.commit()
    db.refresh(r)
    return r


@app.delete("/api/rules/{rule_id}", status_code=204, dependencies=api)
def delete_rule(rule_id: int, db: Session = Depends(get_db)):
    db.delete(_get_or_404(db, SortRule, rule_id))
    db.commit()


@app.post("/api/rules/apply", dependencies=api)
def apply_rules(db: Session = Depends(get_db)):
    moved = sort_rules.apply_to_inbox(db)
    db.commit()
    return {"moved": moved}


# ---------- study coach ----------

class CoachIn(BaseModel):
    message: str = Field(min_length=1, max_length=8000)


class CoachMsgOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    role: str
    content: str


@app.get("/api/coach", dependencies=api)
def coach_status():
    return {"enabled": coach.enabled(), "model": coach.MODEL}


@app.get("/api/tasks/{task_id}/coach", response_model=list[CoachMsgOut], dependencies=api)
def coach_history(task_id: int, db: Session = Depends(get_db)):
    return coach.history(db, _get_or_404(db, Task, task_id))


@app.post("/api/tasks/{task_id}/coach", response_model=CoachMsgOut, dependencies=api)
def coach_ask(task_id: int, body: CoachIn, db: Session = Depends(get_db)):
    task = _get_or_404(db, Task, task_id)
    text = body.message.strip()
    if not text:
        raise HTTPException(status_code=422, detail="Type a question first")
    try:
        return coach.ask(db, task, text)
    except coach.CoachError as exc:
        db.rollback()
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.delete("/api/tasks/{task_id}/coach", status_code=204, dependencies=api)
def coach_clear(task_id: int, db: Session = Depends(get_db)):
    task = _get_or_404(db, Task, task_id)
    for m in list(task.coach_messages):
        db.delete(m)
    db.commit()


# ---------- front end ----------

@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
