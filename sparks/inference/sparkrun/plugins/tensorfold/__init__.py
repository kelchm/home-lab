"""SparkRun 0.3.10 external TensorFold runtime (runtime_name=\"tensorfold\").

Engine/rank adapter for any TensorFold build served on two ranks. Cluster
lifecycle, fabric detection, image/model distribution, caches,
start/status/log/stop and the rendezvous gate stay on SparkRun's native
RuntimePlugin path. The adapter names no model or engine: each recipe sets its
entrypoint, served name and window, and its mod owns the rank launch. SparkRun
0.3.10 treats HTTP 200 as ready, so each recipe's post_exec gate checks the
engine's own health.
"""

from __future__ import annotations

import shlex
from contextvars import ContextVar
from typing import Any, Mapping

from sparkrun.core.parallelism import extract_parallelism
from sparkrun.core.readiness import OPENAI_CHAT_STREAM
from sparkrun.core.runtime_cache import CachePath
from sparkrun.runtimes._util import default_env_hf_offline
from sparkrun.runtimes.base import RuntimePlugin

HF_HUB_ROOT = "/cache/huggingface/hub"
REQUIRED_NODES = 2
REQUIRED_TP = 2
REQUIRED_PP = 1
# Set by every recipe; the adapter has no default engine or model.
REQUIRED_DEFAULTS = ("entrypoint", "served_model_name", "context")

# The head's CX7 address for the launch in progress, per context so nested or concurrent launches keep their own.
_FABRIC_MASTER: ContextVar[str | None] = ContextVar("tensorfold_fabric_master", default=None)

_HF_MODELS_PREFIX = "models--"
_SNAPSHOTS_SEGMENT = "/snapshots/"

_CONFIG_KEYS = frozenset(
    {
        "model_path",
        "drafter",
        "drafter_revision",
        "context",
        "max_tokens",
        "extra_args",
        "mtp_drafts",
        "no_drafts",
        "entrypoint",
    }
)


def hf_snapshot_path(repo_id: str, revision: str) -> str:
    """Immutable HF snapshot path under the SparkRun cache mount."""
    return "%s/%s%s/snapshots/%s" % (HF_HUB_ROOT, _HF_MODELS_PREFIX, repo_id.replace("/", "--"), revision)


def parse_hf_snapshot(path: str) -> tuple[str, str] | None:
    """Return ``(repo_id, revision)`` from a hub snapshot path, if it is one."""
    text = str(path).replace("\\", "/")
    marker = "/%s" % _HF_MODELS_PREFIX
    start = text.find(marker)
    if start < 0:
        if text.startswith(_HF_MODELS_PREFIX):
            rest = text[len(_HF_MODELS_PREFIX) :]
        else:
            return None
    else:
        rest = text[start + len(marker) :]
    snap = rest.find(_SNAPSHOTS_SEGMENT)
    if snap < 0:
        return None
    repo = rest[:snap].replace("--", "/")
    revision = rest[snap + len(_SNAPSHOTS_SEGMENT) :].strip("/")
    if not repo or not revision or "/" in revision:
        return None
    return repo, revision.split("/", 1)[0]


def _config_get(config, key: str, default: Any = None) -> Any:
    value = config.get(key)
    if value is None or value == "":
        return default
    return value


def _env_assignment(name: str, value: Any) -> str:
    return "%s=%s" % (name, shlex.quote(str(value)))


class TensorFoldRuntime(RuntimePlugin):
    """Two-node TensorFold TP2 adapter on SparkRun native clustering."""

    runtime_name = "tensorfold"
    readiness_styles = (OPENAI_CHAT_STREAM,)
    readiness_health_path = "/health"
    supports_heterogeneous_images = False

    def cluster_strategy(self) -> str:
        return "native"

    def prefer_ib_for_init_addr(self) -> bool:
        return True

    def native_api_options(self) -> list[str]:
        return ["chat_completions"]

    def known_config_keys(self) -> frozenset[str]:
        return _CONFIG_KEYS

    def serve_flag_map(self) -> Mapping[str, str] | None:
        return {}

    def managed_rendezvous_flags(self) -> tuple[str, ...]:
        return ()

    def model_revision_flags(self) -> tuple[str, ...]:
        return ()

    def default_executor_config(self) -> dict[str, Any]:
        return {
            "entrypoint": "",
            "ipc": "host",
            "devices": ["/dev/infiniband"],
            "cap_add": ["IPC_LOCK"],
            "ulimit": ["memlock=-1:-1"],
        }

    def get_common_env(self):
        return default_env_hf_offline()

    def runtime_cache_paths(self, *, fingerprint: str = "") -> dict[str, CachePath]:
        del fingerprint
        return {
            "CUDA_CACHE_PATH": CachePath("nv/ComputeCache"),
            "TRITON_CACHE_DIR": CachePath("triton"),
            "TORCH_EXTENSIONS_DIR": CachePath("torch_extensions"),
            "TORCHINDUCTOR_CACHE_DIR": CachePath("inductor"),
        }

    def native_rendezvous_port(
        self,
        recipe,
        overrides: dict[str, Any] | None = None,
        *,
        num_nodes: int = 1,
        init_port: int = 25000,
    ) -> int | None:
        # Rank 0 listens on MASTER:MASTER_PORT before rank 1 starts.
        del recipe, overrides, num_nodes
        return init_port

    def validate_recipe(self, recipe) -> list:
        issues = super().validate_recipe(recipe)
        issues.extend(self._topology_issues(recipe, {}))
        config = recipe.build_config_chain({})
        for key in REQUIRED_DEFAULTS:
            if _config_get(config, key) is None:
                issues.append(self.recipe_error("TensorFold recipes set defaults.%s" % key))
        try:
            self._model_path(recipe, config)
        except ValueError as error:
            issues.append(self.recipe_error(str(error)))
        return issues

    def prepare(self, recipe, hosts, config=None, dry_run: bool = False, transfer_mode: str = "auto", overrides=None) -> None:
        del hosts, config, dry_run, transfer_mode
        chain = recipe.build_config_chain(overrides or {})
        spec = self._drafter_repo(chain)
        if spec:
            recipe.distribution_config.add_model(spec[0], revision=spec[1])

    def generate_command(
        self,
        recipe,
        overrides: dict[str, Any],
        is_cluster: bool,
        num_nodes: int = 1,
        head_ip: str | None = None,
        skip_keys: set[str] | frozenset[str] = frozenset(),
    ) -> str:
        if not is_cluster:
            raise ValueError(self._topology_message("solo/single-node launches are unsupported"))
        return self.generate_node_command(
            recipe,
            overrides,
            head_ip=head_ip or "127.0.0.1",
            num_nodes=num_nodes,
            node_rank=0,
            skip_keys=skip_keys,
        )

    def generate_node_command(
        self,
        recipe,
        overrides: dict[str, Any],
        head_ip: str,
        num_nodes: int,
        node_rank: int,
        init_port: int = 25000,
        skip_keys: set[str] | frozenset[str] = frozenset(),
        hosts: list[str] | None = None,
        placement=None,
    ) -> str:
        self._require_launch_topology(recipe, overrides, num_nodes, node_rank)
        config = recipe.build_config_chain(overrides)
        node_args = self._make_node_command_args(
            head_ip=head_ip,
            num_nodes=num_nodes,
            node_rank=node_rank,
            init_port=init_port,
            hosts=hosts,
            placement=placement,
            replica_size=num_nodes,
        )
        engine_env = self._engine_env(recipe, config, skip_keys)
        engine_env["RANK"] = str(node_rank)
        engine_env["MASTER"] = node_args["master_addr"]
        engine_env["MASTER_PORT"] = node_args["master_port"]
        fabric = _FABRIC_MASTER.get()
        if fabric:
            engine_env["FABRIC_MASTER"] = fabric
        binary = _config_get(config, "entrypoint")
        if binary is None:
            raise ValueError("TensorFold recipes set defaults.entrypoint")
        rendered = recipe.render_command(config)
        command = rendered.strip() if rendered else str(binary)
        ordered = ("RANK", "MASTER", "MASTER_PORT", "FABRIC_MASTER", "MODEL_PATH", "DRAFTER", "CONTEXT", "SERVED_NAME", "MAX_TOKENS", "HOST", "PORT", "EXTRA_ARGS", "MTP_DRAFTS", "NO_DRAFTS")
        assignments = [_env_assignment(key, engine_env[key]) for key in ordered if key in engine_env]
        for key, value in engine_env.items():
            if key not in ordered:
                assignments.append(_env_assignment(key, value))
        return "env %s %s" % (" ".join(assignments), command)

    def _run_cluster(self, hosts, image, serve_command="", recipe=None, overrides=None, **kwargs):
        # The head's CX7 address, for engines whose ranks link over the fabric rather than the rendezvous network.
        token = _FABRIC_MASTER.set((kwargs.get("ib_ip_map") or {}).get(hosts[0]) if hosts else None)
        try:
            return self._run_native_cluster(
                hosts=hosts,
                image=image,
                serve_command=serve_command,
                recipe=recipe,
                overrides=overrides,
                banner_title="TensorFold Cluster Launcher",
                port_label="Master Port",
                node_label="tensorfold rank",
                **kwargs,
            )
        finally:
            _FABRIC_MASTER.reset(token)

    def _stop_cluster(self, hosts, cluster_id, config=None, dry_run: bool = False):
        return self._stop_native_cluster(hosts, cluster_id, config=config, dry_run=dry_run)

    def _require_launch_topology(self, recipe, overrides, num_nodes: int, node_rank: int) -> None:
        if num_nodes != REQUIRED_NODES:
            raise ValueError(self._topology_message("got %d node(s)" % num_nodes))
        if node_rank not in (0, 1):
            raise ValueError(self._topology_message("got RANK=%s" % node_rank))
        issues = self._parallelism_issues(recipe, overrides)
        if issues:
            raise ValueError(issues[0].message)

    def _topology_issues(self, recipe, overrides: dict[str, Any] | None) -> list:
        issues = []
        min_nodes = getattr(recipe, "min_nodes", 1)
        max_nodes = getattr(recipe, "max_nodes", None)
        if min_nodes != REQUIRED_NODES:
            issues.append(self.recipe_error(self._topology_message("min_nodes=%s" % min_nodes)))
        if max_nodes is not None and max_nodes != REQUIRED_NODES:
            issues.append(self.recipe_error(self._topology_message("max_nodes=%s" % max_nodes)))
        issues.extend(self._parallelism_issues(recipe, overrides))
        return issues

    def _parallelism_issues(self, recipe, overrides: dict[str, Any] | None) -> list:
        issues = []
        parallelism = extract_parallelism(recipe.build_config_chain(overrides or {}))
        if parallelism.tensor_parallel != REQUIRED_TP:
            issues.append(self.recipe_error(self._topology_message("tensor_parallel=%s" % parallelism.tensor_parallel)))
        if parallelism.pipeline_parallel != REQUIRED_PP:
            issues.append(self.recipe_error(self._topology_message("pipeline_parallel=%s" % parallelism.pipeline_parallel)))
        if parallelism.data_parallel != 1:
            issues.append(self.recipe_error(self._topology_message("data_parallel=%s" % parallelism.data_parallel)))
        return issues

    def _topology_message(self, detail: str) -> str:
        return (
            "TensorFold requires exactly two nodes, one GPU per rank, tensor_parallel=2, "
            "pipeline_parallel=1 (%s)" % detail
        )

    def _engine_env(self, recipe, config, skip_keys: set[str] | frozenset[str]) -> dict[str, str]:
        env: dict[str, str] = {
            "MODEL_PATH": self._model_path(recipe, config),
            "CONTEXT": str(_config_get(config, "context")),
            "SERVED_NAME": str(_config_get(config, "served_model_name")),
            "HOST": str(_config_get(config, "host", "0.0.0.0")),
            "PORT": str(_config_get(config, "port", "8888")),
        }
        drafter = self._drafter_path(config)
        if drafter:
            env["DRAFTER"] = drafter
        max_tokens = _config_get(config, "max_tokens")
        if max_tokens is not None:
            env["MAX_TOKENS"] = str(max_tokens)
        extra_args = _config_get(config, "extra_args")
        if extra_args:
            env["EXTRA_ARGS"] = str(extra_args)
        mtp = _config_get(config, "mtp_drafts")
        if mtp is not None:
            env["MTP_DRAFTS"] = str(mtp)
        no_drafts = _config_get(config, "no_drafts")
        if no_drafts is not None and str(no_drafts).lower() in {"1", "true", "yes", "on"}:
            env["NO_DRAFTS"] = "1"
        skip_env = {
            "served_model_name": "SERVED_NAME",
            "host": "HOST",
            "port": "PORT",
            "max_model_len": "CONTEXT",
            "context": "CONTEXT",
            "model_path": "MODEL_PATH",
            "drafter": "DRAFTER",
            "max_tokens": "MAX_TOKENS",
            "extra_args": "EXTRA_ARGS",
        }
        for key in skip_keys:
            env.pop(skip_env.get(key, key.upper()), None)
        return env

    def _model_path(self, recipe, config) -> str:
        explicit = _config_get(config, "model_path")
        if explicit:
            return str(explicit)
        model = str(recipe.model or "")
        if model.startswith("/"):
            return model
        revision = str(_config_get(config, "model_revision", getattr(recipe, "model_revision", "") or "") or "")
        if model and revision:
            return hf_snapshot_path(model, revision)
        raise ValueError("MODEL_PATH requires defaults.model_path or model plus model_revision")

    def _drafter_path(self, config) -> str | None:
        drafter = _config_get(config, "drafter")
        if drafter is None:
            return None
        text = str(drafter)
        if text.startswith("/") or parse_hf_snapshot(text):
            return text
        revision = _config_get(config, "drafter_revision")
        if revision:
            return hf_snapshot_path(text, str(revision))
        return text

    def _drafter_repo(self, config) -> tuple[str, str | None] | None:
        drafter = _config_get(config, "drafter")
        if drafter is None:
            return None
        text = str(drafter)
        parsed = parse_hf_snapshot(text)
        if parsed:
            return parsed
        if text.startswith("/"):
            return None
        if "/" in text and not text.startswith("."):
            revision = _config_get(config, "drafter_revision")
            return text, str(revision) if revision else None
        return None
