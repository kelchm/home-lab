"""Control-plane API for the DGX Spark inference pair.

Git owns what can run (profiles on main); this API owns what runs now, by
invoking the same playbooks the operator runs from a laptop. It never switches
on its own: every mutation is an explicit authenticated request.
"""

import asyncio
import contextlib
import time

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

from . import state as views
from .auth import require_token
from .config import Settings
from .jobs import Busy, JobManager


async def _git(*args: str) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        "git", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    out, _ = await proc.communicate()
    return proc.returncode or 0, out.decode(errors="replace").strip()


async def sync_repo(settings: Settings, sync_state: dict) -> None:
    if (settings.repo_dir / ".git").is_dir():
        for args in (
            ("-C", str(settings.repo_dir), "fetch", "--quiet", "origin", "main"),
            ("-C", str(settings.repo_dir), "reset", "--quiet", "--hard", "origin/main"),
        ):
            rc, out = await _git(*args)
            if rc != 0:
                raise RuntimeError(f"git {args[2]} failed: {out}")
    else:
        rc, out = await _git(
            "clone", "--quiet", "--depth", "1", "--branch", "main",
            settings.repo_url, str(settings.repo_dir),
        )
        if rc != 0:
            raise RuntimeError(f"git clone failed: {out}")
    _, rev = await _git("-C", str(settings.repo_dir), "rev-parse", "HEAD")
    sync_state.update(rev=rev, synced_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), error=None)


class ProfileRequest(BaseModel):
    profile: str


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    jobs = JobManager(ansible_dir=settings.ansible_dir, jobs_dir=settings.jobs_dir)
    sync_state: dict = {"rev": None, "synced_at": None, "error": None}

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.token = settings.token_file.read_text().strip()
        settings.jobs_dir.mkdir(parents=True, exist_ok=True)
        sync_task = None
        if settings.sync_interval > 0:
            async def sync_loop() -> None:
                while True:
                    # A moving checkout under a running playbook is never
                    # acceptable; the job lock covers the sync too.
                    if not jobs.busy:
                        lock = await jobs.hold()
                        async with lock:
                            try:
                                await sync_repo(settings, sync_state)
                            except Exception as exc:  # noqa: BLE001 - keep syncing
                                sync_state["error"] = str(exc)
                    await asyncio.sleep(settings.sync_interval)

            sync_task = asyncio.get_running_loop().create_task(sync_loop())
        yield
        if sync_task:
            sync_task.cancel()

    app = FastAPI(title="spark-controller", lifespan=lifespan)
    authed = [Depends(require_token)]

    def known_profiles() -> dict[str, dict]:
        if not settings.ansible_dir.is_dir():
            raise HTTPException(status_code=503, detail="repo checkout not synced yet")
        return views.read_profiles(settings.ansible_dir)

    @app.get("/admin/v1/healthz")
    async def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/admin/v1/state", dependencies=authed)
    async def get_state() -> dict:
        profiles = views.read_profiles(settings.ansible_dir) if settings.ansible_dir.is_dir() else {}
        return {
            "resident": views.read_resident(settings.state_dir),
            "default_profile": views.read_default_profile(settings.ansible_dir),
            "profiles": sorted(profiles),
            "endpoints": await views.probe_endpoints(profiles),
            "git": sync_state,
            "job": jobs.current.meta() if jobs.current else None,
            "switch_log": views.read_switch_log_tail(settings.state_dir),
        }

    @app.get("/admin/v1/profiles", dependencies=authed)
    async def get_profiles() -> dict:
        return known_profiles()

    @app.get("/admin/v1/jobs", dependencies=authed)
    async def list_jobs() -> list[dict]:
        return jobs.list()

    @app.get("/admin/v1/jobs/{job_id}", dependencies=authed)
    async def get_job(job_id: str, log_offset: int = 0) -> dict:
        meta = jobs.get(job_id)
        if meta is None:
            raise HTTPException(status_code=404, detail="no such job")
        return {**meta, "log": jobs.log_text(job_id, log_offset)}

    async def submit(kind: str, profile: str | None = None) -> dict:
        try:
            job = await jobs.start(kind, profile)
        except Busy as exc:
            raise HTTPException(
                status_code=409,
                detail={"message": "a job is already running", "job": jobs.current.meta() if jobs.current else None},
            ) from exc
        return job.meta()

    @app.post("/admin/v1/switches", dependencies=authed, status_code=202)
    async def post_switch(request: ProfileRequest) -> dict:
        if request.profile not in known_profiles():
            raise HTTPException(status_code=422, detail=f"unknown profile {request.profile!r}")
        return await submit("switch", request.profile)

    @app.post("/admin/v1/publishes", dependencies=authed, status_code=202)
    async def post_publish(request: ProfileRequest) -> dict:
        if request.profile not in known_profiles():
            raise HTTPException(status_code=422, detail=f"unknown profile {request.profile!r}")
        return await submit("publish", request.profile)

    @app.post("/admin/v1/down", dependencies=authed, status_code=202)
    async def post_down() -> dict:
        if not settings.ansible_dir.is_dir():
            raise HTTPException(status_code=503, detail="repo checkout not synced yet")
        return await submit("down")

    return app
