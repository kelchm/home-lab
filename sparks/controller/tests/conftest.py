import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402

TOKEN = "test-token"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    token_file = tmp_path / "token"
    token_file.write_text(TOKEN + "\n")
    ansible_dir = tmp_path / "repo" / "sparks" / "ansible"
    (ansible_dir / "profiles").mkdir(parents=True)
    (ansible_dir / "group_vars").mkdir()
    (ansible_dir / "group_vars" / "all.yaml").write_text("default_profile: qwen\n")
    (ansible_dir / "profiles" / "qwen.yaml").write_text(
        "profile_name: qwen\nserve_port: 8000\nserved_model_id: qwen3.6-35b\n"
    )
    (ansible_dir / "profiles" / "glm.yaml").write_text(
        "profile_name: glm\nserve_port: 8888\nserved_model_id: GLM-5.3-Flash-EXL3\n"
    )
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "resident").write_text("glm\n")
    return Settings(
        port=0,
        token_file=token_file,
        repo_url="https://example.invalid/repo.git",
        repo_dir=tmp_path / "repo",
        jobs_dir=tmp_path / "jobs",
        state_dir=state_dir,
        sync_interval=0,
    )


@pytest.fixture
def client(settings: Settings):
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def auth() -> dict:
    return {"Authorization": f"Bearer {TOKEN}"}
