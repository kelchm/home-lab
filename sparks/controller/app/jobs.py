"""Single-flight execution of the repo's transaction playbooks.

The playbooks own every mutation (teardown, start, Caddy publication,
residency); this layer only serializes invocations and journals them. The
playbook-side lock on spark-1 stays in place as the guard shared with the
operator's direct ansible path.
"""

import asyncio
import json
import time
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
                id=f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{kind}",
                kind=kind,
                profile=profile,
                started_at=_utcnow(),
            )
            self.current = job
            job_dir = self.jobs_dir / job.id
            job_dir.mkdir(parents=True, exist_ok=True)
            self._write_meta(job)
            asyncio.get_running_loop().create_task(self._run(job, job_dir))
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
            job.finished_at = _utcnow()
            # A journal-write failure must never wedge single-flight.
            try:
                self._write_meta(job)
            finally:
                self._lock.release()

    def _write_meta(self, job: Job) -> None:
        (self.jobs_dir / job.id / "meta.json").write_text(json.dumps(job.meta()))

    def get(self, job_id: str) -> dict | None:
        if self.current and self.current.id == job_id:
            return self.current.meta()
        meta_file = self.jobs_dir / job_id / "meta.json"
        if not meta_file.is_file():
            return None
        return json.loads(meta_file.read_text())

    def log_text(self, job_id: str, offset: int = 0) -> str | None:
        log_file = self.jobs_dir / job_id / "job.log"
        if not log_file.is_file():
            return None
        data = log_file.read_bytes()[offset:]
        return data.decode(errors="replace")

    def list(self, limit: int = 20) -> list[dict]:
        metas = []
        for meta_file in sorted(self.jobs_dir.glob("*/meta.json"), reverse=True)[:limit]:
            metas.append(json.loads(meta_file.read_text()))
        return metas
