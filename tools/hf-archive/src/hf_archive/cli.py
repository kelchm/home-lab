"""Command line: separate acquisition commands (pull, import, metadata) and a read-only server."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from . import __version__
from .acquire import DEFAULT_RESERVE_BYTES, import_directory, pull
from .errors import ArchiveError, IntegrityError
from .model import COMMIT_RE, RevisionMetadata
from .store import Store

log = logging.getLogger("hf_archive")


def _root(args: argparse.Namespace) -> Path:
    if not args.root:
        raise ArchiveError("no archive root: pass --root or set HF_ARCHIVE_ROOT")
    return Path(args.root)


def _token(args: argparse.Namespace) -> str | None:
    if args.token_file:
        return Path(args.token_file).read_text().strip() or None
    return os.environ.get("HF_TOKEN") or None


def _hub(args: argparse.Namespace, home: Path):
    from .hub import SdkHub

    return SdkHub(home, token=_token(args), endpoint=args.endpoint)


def _report(args: argparse.Namespace, doc: dict[str, Any], summary: str) -> None:
    if args.json:
        json.dump(doc, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        print(summary)


def _summary(action: str, result) -> str:
    by_via: dict[str, int] = {}
    for file in result.files.values():
        by_via[file["via"]] = by_via.get(file["via"], 0) + 1
    counts = ", ".join(f"{count} via {via}" for via, count in sorted(by_via.items()))
    refs = f", refs {', '.join(result.refs)}" if result.refs else ""
    retained = "".join(
        f"\n  {ref} left at {commit}; pass --move-refs to point it at this commit"
        for ref, commit in result.refs_retained.items()
    )
    return (
        f"{action} {result.repo_id}@{result.commit}: {len(result.files)} files ({counts}); "
        f"revision now holds {result.acquired_total} of {result.tree_total} files{refs}{retained}"
    )


def cmd_pull(args: argparse.Namespace) -> int:
    store = Store.init(_root(args))
    with store.staging() as home:
        result = pull(
            store,
            _hub(args, home),
            args.repo_id,
            args.revision,
            include=args.include,
            exclude=args.exclude,
            select_all=args.all,
            reserve_bytes=args.reserve_bytes,
        )
    _report(args, result.to_json(), _summary("pulled", result))
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    store = Store.init(_root(args))
    mapping = {}
    for item in args.map:
        repo_path, separator, source_path = item.partition("=")
        if not separator or not repo_path or not source_path:
            raise ArchiveError(f"--map takes REPO_PATH=SOURCE_PATH, got {item!r}")
        mapping[repo_path] = source_path
    with store.staging() as home:
        if args.metadata:
            with open(args.metadata, encoding="utf-8") as f:
                meta = RevisionMetadata.from_hub_json(json.load(f))
            if meta.repo_id != args.repo_id:
                raise ArchiveError(f"{args.metadata} describes {meta.repo_id}, not {args.repo_id}")
            if COMMIT_RE.fullmatch(args.revision) and args.revision != meta.commit:
                raise ArchiveError(f"{args.metadata} describes commit {meta.commit}, not {args.revision}")
        else:
            meta = _hub(args, home).fetch_metadata(args.repo_id, args.revision)
        result = import_directory(
            store,
            meta,
            args.source,
            mapping=mapping,
            include=args.include,
            exclude=args.exclude,
            reserve_bytes=args.reserve_bytes,
            # Metadata fetched for this import observed upstream's refs; a saved document is of unknown age.
            move_refs=args.move_refs or not args.metadata,
        )
    _report(args, result.to_json(), _summary("imported", result))
    return 0


def cmd_metadata(args: argparse.Namespace) -> int:
    with tempfile.TemporaryDirectory(prefix="hf-archive-") as home:
        meta = _hub(args, Path(home)).fetch_metadata(args.repo_id, args.revision)
    json.dump(meta.to_hub_json(), sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from .server import make_server

    # The server needs no credential; make sure a stray one cannot be inherited by it.
    for name in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "HF_TOKEN_PATH"):
        if os.environ.pop(name, None):
            log.warning("%s is set but the server uses no token; removed it from the server's environment", name)
    server = make_server(_root(args), args.host, args.port)
    server.warm_in_background()
    log.info("serving %s read-only on http://%s:%d", server.store.root, *server.server_address[:2])
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def _inventory(store: Store) -> list[dict[str, Any]]:
    repos = []
    for repo_id in store.list_repos():
        revisions = []
        for commit in store.list_commits(repo_id):
            manifest = store.read_manifest(repo_id, commit)
            assert manifest is not None
            held = [entry for entry in manifest.files.values() if entry.blob]
            revisions.append(
                {
                    "commit": commit,
                    "files_acquired": len(held),
                    "files_total": len(manifest.files),
                    "bytes_acquired": sum(entry.file.size for entry in held),
                    "updated_at": manifest.updated_at,
                }
            )
        refs = {ref.ref: ref.commit for ref in store.list_refs(repo_id)}
        repos.append({"repo_id": repo_id, "refs": refs, "revisions": revisions})
    return repos


def cmd_list(args: argparse.Namespace) -> int:
    repos = _inventory(Store(_root(args)))
    lines = []
    for repo in repos:
        lines.append(repo["repo_id"])
        lines += [f"  {ref} -> {commit}" for ref, commit in repo["refs"].items()]
        lines += [
            f"  {r['commit']}  {r['files_acquired']}/{r['files_total']} files  {r['bytes_acquired']} bytes"
            for r in repo["revisions"]
        ]
    _report(args, {"repos": repos}, "\n".join(lines) or "archive holds no revisions")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    """Re-read every blob a manifest references and check it against the identity the manifest records."""
    store = Store(_root(args))
    problems = []
    checked = 0
    for repo_id in store.list_repos():
        for commit in store.list_commits(repo_id):
            try:
                manifest = store.read_manifest(repo_id, commit)
            except ArchiveError as e:
                problems.append({"repo_id": repo_id, "commit": commit, "problem": str(e)})
                continue
            assert manifest is not None
            for path, entry in manifest.files.items():
                if entry.blob is None:
                    continue
                checked += 1
                if not store.check_blob(entry.blob, entry.file):
                    problems.append({"repo_id": repo_id, "commit": commit, "path": path, "blob": entry.blob})
    summary = f"checked {checked} file references, {len(problems)} problems"
    for problem in problems:
        summary += "\n  " + " ".join(f"{key}={value}" for key, value in problem.items())
    _report(args, {"checked": checked, "problems": problems}, summary)
    return IntegrityError.exit_code if problems else 0


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", default=os.environ.get("HF_ARCHIVE_ROOT"), help="archive root (or HF_ARCHIVE_ROOT)")
    output = argparse.ArgumentParser(add_help=False)
    output.add_argument("--json", action="store_true", help="print a JSON result on stdout")
    hub = argparse.ArgumentParser(add_help=False)
    hub.add_argument("--revision", default="main", help="branch, tag, full ref, or commit hash (default: main)")
    hub.add_argument("--endpoint", default="https://huggingface.co", help="upstream Hub; HF_ENDPOINT is ignored")
    hub.add_argument("--token-file", help="file holding a read-only HF token (or set HF_TOKEN)")
    select = argparse.ArgumentParser(add_help=False)
    select.add_argument("--include", action="append", default=[], metavar="GLOB", help="repository paths to take")
    select.add_argument("--exclude", action="append", default=[], metavar="GLOB", help="repository paths to skip")
    select.add_argument("--reserve-bytes", type=int, default=DEFAULT_RESERVE_BYTES, help="free space to keep")

    parser = argparse.ArgumentParser(prog="hf-archive", description=__doc__)
    parser.add_argument("--version", action="version", version=f"hf-archive {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    p = commands.add_parser("pull", parents=[common, output, hub, select], help="acquire selected files from the Hub")
    p.add_argument("repo_id")
    p.add_argument("--all", action="store_true", help="take every file of the revision")
    p.set_defaults(run=cmd_pull)

    p = commands.add_parser(
        "import", parents=[common, output, hub, select], help="acquire files from a local directory"
    )
    p.add_argument("repo_id")
    p.add_argument("--source", required=True, help="plain directory, HF snapshot directory, or HF repo cache directory")
    p.add_argument("--metadata", help="metadata document to use instead of asking the Hub")
    p.add_argument("--map", action="append", default=[], metavar="REPO_PATH=SOURCE_PATH", help="renamed source file")
    p.add_argument(
        "--move-refs",
        action="store_true",
        help="with --metadata, also replace refs recorded at another commit (default: only create missing refs)",
    )
    p.set_defaults(run=cmd_import)

    p = commands.add_parser("metadata", parents=[hub], help="print a revision's metadata document for offline import")
    p.add_argument("repo_id")
    p.set_defaults(run=cmd_metadata)

    p = commands.add_parser("serve", parents=[common], help="serve the archive read-only as an HF endpoint")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8080)
    p.set_defaults(run=cmd_serve)

    p = commands.add_parser("list", parents=[common, output], help="show archived repositories, refs and revisions")
    p.set_defaults(run=cmd_list)

    p = commands.add_parser("verify", parents=[common, output], help="re-hash every referenced blob")
    p.set_defaults(run=cmd_verify)
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stderr)
    args = build_parser().parse_args(argv)
    try:
        return args.run(args)
    except ArchiveError as e:
        print(f"hf-archive: {e}", file=sys.stderr)
        return e.exit_code
    except OSError as e:
        print(f"hf-archive: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
