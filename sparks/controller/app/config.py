import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    port: int
    token_file: Path
    repo_url: str
    repo_dir: Path
    jobs_dir: Path
    state_dir: Path
    # Seconds between git syncs of the repo checkout; <= 0 disables syncing.
    sync_interval: int

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.environ
        return cls(
            port=int(env.get("SPARK_CONTROLLER_PORT", "9800")),
            token_file=Path(env.get("SPARK_CONTROLLER_TOKEN_FILE", "/etc/spark-controller/token")),
            repo_url=env.get("SPARK_CONTROLLER_REPO_URL", "https://github.com/kelchm/home-lab.git"),
            repo_dir=Path(env.get("SPARK_CONTROLLER_REPO_DIR", "/var/lib/spark-controller/repo")),
            jobs_dir=Path(env.get("SPARK_CONTROLLER_JOBS_DIR", "/var/lib/spark-controller/jobs")),
            state_dir=Path(env.get("SPARK_CONTROLLER_STATE_DIR", "/opt/spark-stack")),
            sync_interval=int(env.get("SPARK_CONTROLLER_SYNC_INTERVAL", "60")),
        )

    @property
    def ansible_dir(self) -> Path:
        return self.repo_dir / "sparks" / "ansible"
