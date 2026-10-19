from datetime import date, datetime, timezone

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, deferred, mapped_column, relationship

from .db import Base


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    color: Mapped[str] = mapped_column(String(16), default="#5b8def")
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | paused | done
    next_step: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

    tasks: Mapped[list["Task"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    attachments: Mapped[list["Attachment"]] = relationship(back_populates="project", cascade="all, delete-orphan")


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(300))
    priority: Mapped[int] = mapped_column(Integer, default=1)  # 0 low, 1 normal, 2 high
    due: Mapped[date | None] = mapped_column(Date, nullable=True)
    today: Mapped[bool] = mapped_column(Boolean, default=False)  # pinned to "Today"
    done: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source: Mapped[str | None] = mapped_column(String(20), nullable=True)  # "schoology" for imported tasks
    link: Mapped[str | None] = mapped_column(String(500), nullable=True)  # back to the original assignment

    project: Mapped[Project | None] = relationship(back_populates="tasks")
    attachments: Mapped[list["Attachment"]] = relationship(back_populates="task", cascade="all, delete-orphan")


class SyncItem(Base):
    """Remembers every calendar event already imported, so deleted tasks don't come back."""
    __tablename__ = "sync_items"

    uid: Mapped[str] = mapped_column(String(300), primary_key=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


class Attachment(Base):
    """An image or PDF pinned to a task (an assignment) or a project (a syllabus)."""
    __tablename__ = "attachments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id"), nullable=True, index=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), nullable=True, index=True)
    filename: Mapped[str] = mapped_column(String(200))
    content_type: Mapped[str] = mapped_column(String(50))
    size: Mapped[int] = mapped_column(Integer)
    # Only loaded when the file itself is downloaded
    data: Mapped[bytes] = deferred(mapped_column(LargeBinary))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

    task: Mapped[Task | None] = relationship(back_populates="attachments")
    project: Mapped[Project | None] = relationship(back_populates="attachments")
