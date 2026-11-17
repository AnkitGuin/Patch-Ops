import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    Float,
    ForeignKey,
    Integer,
    String,
    create_engine,
    event,
    select,
    update,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from .config import Settings
from .models import TERMINAL, TRANSITIONS, Request, State
from .security import Blocked, Cancelled, redact, safe_file


def now() -> float:
    return datetime.now(UTC).timestamp()


def iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, UTC).isoformat()


class Base(DeclarativeBase):
    pass


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    state: Mapped[str] = mapped_column(String(40), index=True)
    label: Mapped[str] = mapped_column(String(160))
    request: Mapped[dict[str, Any]] = mapped_column(JSON)
    created: Mapped[float] = mapped_column(Float, index=True)
    updated: Mapped[float] = mapped_column(Float)
    started: Mapped[float | None] = mapped_column(Float)
    finished: Mapped[float | None] = mapped_column(Float)
    owner: Mapped[str | None] = mapped_column(String(32))
    lease_until: Mapped[float | None] = mapped_column(Float)
    cancelled: Mapped[bool] = mapped_column(Boolean, default=False)
    pr_url: Mapped[str | None] = mapped_column(String(500))


class Record(Base):
    __tablename__ = "job_records"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(48), index=True)
    stage: Mapped[str] = mapped_column(String(40))
    created: Mapped[float] = mapped_column(Float)
    data: Mapped[dict[str, Any]] = mapped_column(JSON)


class Store:
    def __init__(self, settings: Settings):
        self.root = settings.data_root.resolve()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if settings.database_url.startswith("sqlite:///"):
            path = settings.database_url.removeprefix("sqlite:///")
            if path != ":memory:":
                Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(settings.database_url, pool_pre_ping=True)
        if self.engine.dialect.name == "sqlite":
            event.listen(self.engine, "connect", self._sqlite)
            # SQLite quickstart. PostgreSQL schema is managed by Alembic.
            Base.metadata.create_all(self.engine)

    @staticmethod
    def _sqlite(connection: Any, _: Any) -> None:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")

    def folder(self, job_id: str) -> Path:
        if len(job_id) != 32 or any(char not in "0123456789abcdef" for char in job_id):
            raise Blocked("Invalid job ID")
        return self.root / job_id

    def submit(self, request: Request, label: str, upload: Path | None = None) -> str:
        job_id = uuid4().hex
        folder = self.folder(job_id)
        folder.mkdir(mode=0o700)
        try:
            if upload is not None:
                upload.replace(folder / "input.zip")
            with Session(self.engine) as session:
                timestamp = now()
                session.add(
                    Job(
                        id=job_id,
                        label=redact(label)[:160],
                        state=State.CREATED,
                        request=request.model_dump(mode="json"),
                        created=timestamp,
                        updated=timestamp,
                    )
                )
                session.flush()
                self.record(
                    session, job_id, State.CREATED, "queued", {"message": "Job queued"}
                )
                session.commit()
        except BaseException:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        return job_id

    def get(self, job_id: str) -> Job | None:
        with Session(self.engine) as session:
            return session.get(Job, job_id)

    def listing(self, limit: int, offset: int, state: str | None) -> list[Job]:
        with Session(self.engine) as session:
            query = select(Job).order_by(Job.created.desc()).limit(limit).offset(offset)
            if state:
                query = query.where(Job.state == state)
            return list(session.scalars(query))

    @staticmethod
    def record(
        session: Session, job_id: str, stage: str, kind: str, data: dict[str, Any]
    ) -> None:
        session.add(
            Record(job_id=job_id, stage=stage, kind=kind, created=now(), data=data)
        )

    def records(
        self, job_id: str, kind: str | None = None, limit: int = 100, offset: int = 0
    ) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            query = (
                select(Record)
                .where(Record.job_id == job_id)
                .order_by(Record.id)
                .limit(limit)
                .offset(offset)
            )
            if kind:
                query = query.where(Record.kind == kind)
            return [
                {
                    "id": row.id,
                    "type": row.kind,
                    "stage": row.stage,
                    "time": iso(row.created),
                    "data": row.data,
                }
                for row in session.scalars(query)
            ]

    def claim(self, owner: str, lease: int = 45) -> str | None:
        with Session(self.engine) as session:
            job_id = session.scalar(
                select(Job.id)
                .where(Job.state == State.CREATED, Job.owner.is_(None))
                .order_by(Job.created)
                .limit(1)
            )
            if job_id is None:
                return None
            timestamp = now()
            result = session.execute(
                update(Job)
                .where(
                    Job.id == job_id, Job.state == State.CREATED, Job.owner.is_(None)
                )
                .values(owner=owner, lease_until=timestamp + lease, started=timestamp)
                .returning(Job.id)
            )
            claimed = result.scalar_one_or_none()
            session.commit()
            return claimed

    def heartbeat(self, job_id: str, owner: str, lease: int = 45) -> bool:
        with Session(self.engine) as session:
            timestamp = now()
            result = session.execute(
                update(Job)
                .where(
                    Job.id == job_id, Job.owner == owner, Job.lease_until > timestamp
                )
                .values(lease_until=timestamp + lease)
                .returning(Job.id)
            )
            renewed = result.scalar_one_or_none()
            session.commit()
            return renewed is not None

    def recover(self) -> list[str]:
        recovered = []
        with Session(self.engine) as session:
            timestamp = now()
            for job in session.scalars(
                select(Job).where(Job.owner.is_not(None), Job.lease_until <= timestamp)
            ):
                result = session.execute(
                    update(Job)
                    .where(Job.id == job.id, Job.lease_until <= timestamp)
                    .values(
                        state=State.FAILED,
                        finished=timestamp,
                        updated=timestamp,
                        owner=None,
                        lease_until=None,
                    )
                    .returning(Job.id)
                )
                if result.scalar_one_or_none() is not None:
                    recovered.append(job.id)
                    self.record(
                        session,
                        job.id,
                        State.FAILED,
                        "lease_expired",
                        {"message": "Worker lease expired. Submit a new job to retry."},
                    )
            session.commit()
        return recovered

    def transition(
        self, job_id: str, owner: str, target: State, message: str = ""
    ) -> None:
        with Session(self.engine) as session:
            job = session.get(Job, job_id)
            if job is None or job.owner != owner or (job.lease_until or 0) <= now():
                raise Blocked("Job lease was lost")
            current = State(job.state)
            exceptional = {State.BLOCKED, State.FAILED, State.CANCELLED}
            if (
                current in TERMINAL
                or target not in TRANSITIONS.get(current, set()) | exceptional
            ):
                raise Blocked(f"Illegal state transition {current} -> {target}")
            timestamp = now()
            values: dict[str, Any] = {"state": target, "updated": timestamp}
            if target in TERMINAL:
                values.update(finished=timestamp, owner=None, lease_until=None)
            result = session.execute(
                update(Job)
                .where(
                    Job.id == job_id,
                    Job.owner == owner,
                    Job.lease_until > timestamp,
                    Job.state == current,
                )
                .values(**values)
                .returning(Job.id)
            )
            if result.scalar_one_or_none() is None:
                raise Blocked("Job lease was lost")
            self.record(
                session, job_id, target, "state_changed", {"message": message or target}
            )
            session.commit()

    def checkpoint(self, job_id: str, owner: str) -> None:
        job = self.get(job_id)
        if job is None or job.owner != owner or (job.lease_until or 0) <= now():
            raise Blocked("Job lease was lost")
        if job.cancelled:
            raise Cancelled("Cancellation requested")

    def cancel(self, job_id: str) -> None:
        with Session(self.engine) as session:
            result = session.execute(
                update(Job)
                .where(
                    Job.id == job_id,
                    Job.state.not_in(TERMINAL),
                    Job.cancelled.is_(False),
                )
                .values(cancelled=True, updated=now())
                .returning(Job.state)
            )
            state = result.scalar_one_or_none()
            if state:
                self.record(
                    session,
                    job_id,
                    state,
                    "cancel_requested",
                    {"message": "Cancellation requested"},
                )
            session.commit()

    def add(self, job_id: str, owner: str, kind: str, data: dict[str, Any]) -> None:
        with Session(self.engine) as session:
            job = session.scalar(
                select(Job)
                .where(Job.id == job_id, Job.owner == owner, Job.lease_until > now())
                .with_for_update()
            )
            if job is None:
                raise Blocked("Job lease was lost")
            self.record(session, job_id, job.state, kind, data)
            session.commit()

    def write(self, job_id: str, name: str, data: str | dict[str, Any]) -> Path:
        path = self.folder(job_id) / name
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_text(
                data
                if isinstance(data, str)
                else json.dumps(data, indent=2, ensure_ascii=False),
                encoding="utf-8",
                newline="\n",
            )
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        return path

    def artifact(self, job_id: str, name: str) -> Path:
        return safe_file(self.folder(job_id), name)
