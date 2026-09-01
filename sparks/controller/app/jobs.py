"""Single-flight execution of the repo's transaction playbooks.

The playbooks own every mutation (teardown, start, Caddy publication,
residency); this layer only serializes invocations and journals them. The
playbook-side lock on spark-1 stays in place as the guard shared with the
operator's direct ansible path.
"""

import asyncio
import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

PLAYBOOKS = {
    "switch": "switch.yaml",
    "publish": "publish.yaml",
    "down": "down.yaml",
}


class Busy(Exception):
    pass


def default_command(kind: str, profile: str | None) -> list[str]:
    cmd = ["ansible-playbook", PLAYBOOKS[kind]]
    if profile is not None:
        cmd += ["-e", f"profile={profile}"]
    return cmd


@dataclass
class Job:
    id: str
    kind: str
    profile: str | None
    started_at: str
    finished_at: str | None = None
    rc: int | None = None

    @property
    def status(self) -> str:
        if self.rc is None:
            return "running"
        return "succeeded" if self.rc == 0 else "failed"

    def meta(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "profile": self.profile,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "rc": self.rc,
        }


def _utcnow() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@dataclass
class JobManager:
    ansible_dir: Path
    jobs_dir: Path
    # Resolved at call time so tests can substitute the module-level builder.
    command: Callable[[str, str | None], list[str]] | None = None

    def __post_init__(self) -> None:
        self._lock = asyncio.Lock()
        self.current: Job | None = None
        # The event loop keeps only weak references to tasks; without this a
        # running job could be garbage-collected mid-execution.
        self._tasks: set[asyncio.Task] = set()

    @property
    def busy(self) -> bool:
        return self._lock.locked()

    async def hold(self) -> "asyncio.Lock":
        """Lets the git sync take the same exclusivity as a job."""
        return self._lock

    async def start(self, kind: str, profile: str | None = None) -> Job:
        if kind not in PLAYBOOKS:
            raise ValueError(f"unknown job kind {kind!r}")
        if self._lock.locked():
            raise Busy
        await self._lock.acquire()
        # Once _run is scheduled, its finally owns the release; until then a
        # setup failure (journal dir, meta write) must not wedge single-flight.
        try:
            job = Job(
                # The random suffix keeps two same-kind jobs submitted within
                # one second from sharing a journal directory.
                id=f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{kind}-{uuid.uuid4().hex[:6]}",
                kind=kind,
                profile=profile,
                started_at=_utcnow(),
            )
            self.current = job
            job_dir = self.jobs_dir / job.id
            job_dir.mkdir(parents=True, exist_ok=False)
            self._write_meta(job)
            task = asyncio.get_running_loop().create_task(self._run(job, job_dir))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        except BaseException:
            self._lock.release()
            raise
        return job

    async def _run(self, job: Job, job_dir: Path) -> None:
        try:
            build_command = self.command if self.command is not None else default_command
            with (job_dir / "job.log").open("wb") as log:
                proc = await asyncio.create_subprocess_exec(
                    *build_command(job.kind, job.profile),
                    cwd=self.ansible_dir,
                    stdout=log,
                    stderr=asyncio.subprocess.STDOUT,
                )
                job.rc = await proc.wait()
        except Exception as exc:  # noqa: BLE001 - journal the failure, never crash the loop
            job.rc = -1
            with (job_dir / "job.log").open("ab") as log:
                log.write(f"controller error: {exc}\n".encode())
        finally:
            # Cancellation (e.g. controller shutdown mid-job) lands here too;
            # never journal a job as running forever.
            if job.rc is None:
                job.rc = -1
            job.finished_at = _utcnow()
            # A journal-write failure must never wedge single-flight.
            try:
                self._write_meta(job)
            finally:
                self._lock.release()

    def _write_meta(self, job: Job) -> None:
        # Atomic replace: termination mid-write must not leave truncated JSON.
        meta_file = self.jobs_dir / job.id / "meta.json"
        tmp_file = meta_file.with_suffix(".json.tmp")
        tmp_file.write_text(json.dumps(job.meta()))
        tmp_file.replace(meta_file)

    @staticmethod
    def _read_meta(meta_file: Path) -> dict:
        try:
            return json.loads(meta_file.read_text())
        except (OSError, json.JSONDecodeError):
            return {"id": meta_file.parent.name, "status": "corrupt-journal"}

    def get(self, job_id: str) -> dict | None:
        if self.current and self.current.id == job_id:
            return self.current.meta()
        meta_file = self.jobs_dir / job_id / "meta.json"
        if not meta_file.is_file():
            return None
        return self._read_meta(meta_file)

    def log_text(self, job_id: str, offset: int = 0) -> str | None:
        log_file = self.jobs_dir / job_id / "job.log"
        if not log_file.is_file():
            return None
        data = log_file.read_bytes()[offset:]
        return data.decode(errors="replace")

    def list(self, limit: int = 20) -> list[dict]:
        meta_files = sorted(self.jobs_dir.glob("*/meta.json"), reverse=True)[:limit]
        return [self._read_meta(meta_file) for meta_file in meta_files]
