"""BioMysteryBench problems and data: download, extract, load.

Layout under `data/<dataset>/`:

    meta/problems.csv      id, question, answer_rubric, allowed_domains, human_solvable
    meta/README.md, meta/CHANGELOG.md
    tasks/<id>/...         that problem's data files, as the agent will see them

`meta/` holds the answer rubrics. Nothing under it is ever mounted into a sandbox:
the runner hands an agent `Problem.public()`, and only `tasks/<id>/` is mounted.
"""

from __future__ import annotations

import csv
import os
import shutil
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path

from bmbench.config import resolve

REPOS = {
    "preview": "Anthropic/BioMysteryBench-preview",
    "full": "Anthropic/BioMysteryBench-full",
}
META_FILES = ("problems.csv", "README.md", "CHANGELOG.md")
PREPARED_MARKER = ".bmbench_prepared"
_SKIP_PARTS = {"__MACOSX", ".DS_Store"}


@dataclass(frozen=True)
class Problem:
    id: str
    question: str
    answer_rubric: str
    allowed_domains: tuple[str, ...]
    human_solvable: str  # "yes" | "no"

    def public(self) -> dict[str, object]:
        """What an agent may be told. Never the rubric."""
        return {
            "id": self.id,
            "question": self.question,
            "allowed_domains": list(self.allowed_domains),
        }


def dataset_dir(name: str, root: str | Path = "data") -> Path:
    if name not in REPOS:
        raise ValueError(f"unknown dataset {name!r}; one of {sorted(REPOS)}")
    return resolve(root) / name


def task_data_dir(name: str, task_id: str, root: str | Path = "data") -> Path:
    return dataset_dir(name, root) / "tasks" / task_id


def parse_domains(raw: str) -> tuple[str, ...]:
    out: list[str] = []
    for part in (raw or "").replace(";", ",").replace("\n", ",").split(","):
        d = part.strip().lower()
        for prefix in ("https://", "http://"):
            d = d.removeprefix(prefix)
        d = d.strip("/").lstrip("*.").strip(".")
        if d and d not in out:
            out.append(d)
    return tuple(out)


def load_problems(name: str, root: str | Path = "data") -> list[Problem]:
    path = dataset_dir(name, root) / "meta" / "problems.csv"
    if not path.is_file():
        raise FileNotFoundError(f"{path} not found; run `bmbench prepare --dataset {name}` first")
    csv.field_size_limit(sys.maxsize)
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    problems = []
    for row in rows:
        problems.append(
            Problem(
                id=row["id"].strip(),
                question=row["question"].strip(),
                answer_rubric=row["answer_rubric"].strip(),
                allowed_domains=parse_domains(row.get("allowed_domains", "")),
                human_solvable=(row.get("human_solvable") or "").strip().lower(),
            )
        )
    return problems


def select_problems(
    problems: list[Problem], ids: list[str] | None = None, human_solvable: str | None = None
) -> list[Problem]:
    if ids:
        by_id = {p.id: p for p in problems}
        missing = [i for i in ids if i not in by_id]
        if missing:
            raise KeyError(f"unknown task id(s): {', '.join(missing)}")
        problems = [by_id[i] for i in ids]
    if human_solvable:
        problems = [p for p in problems if p.human_solvable == human_solvable.lower()]
    return problems


# -- download / extract ------------------------------------------------------


def safe_extract(zip_path: Path, dest: Path, only_top: set[str] | None = None) -> list[str]:
    """Extract without zip-slip, skipping macOS metadata. Returns top-level names.

    `only_top`: extract only members whose first path component is in this set.
    """
    dest = dest.resolve()
    tops: set[str] = set()
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            parts = [p for p in Path(info.filename).parts if p not in ("", ".")]
            if not parts or any(p in _SKIP_PARTS or p.startswith("._") for p in parts):
                continue
            if only_top is not None and parts[0] not in only_top:
                continue
            target = (dest / Path(*parts)).resolve()
            if target != dest and dest not in target.parents:
                raise ValueError(f"unsafe path in {zip_path.name}: {info.filename!r}")
            tops.add(parts[0])
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out, length=16 * 1024 * 1024)
    return sorted(tops)


def _flatten_single_dir(task_dir: Path) -> None:
    """`tasks/<id>/<id>/files` -> `tasks/<id>/files` when a zip wraps its files."""
    entries = [e for e in task_dir.iterdir() if e.name != PREPARED_MARKER]
    if len(entries) == 1 and entries[0].is_dir():
        inner = entries[0]
        for child in inner.iterdir():
            shutil.move(str(child), task_dir / child.name)
        inner.rmdir()


def _hf_download(repo: str, filename: str, local_dir: Path, token: str | None) -> Path:
    from huggingface_hub import hf_hub_download  # noqa: PLC0415 - optional at import time

    return Path(
        hf_hub_download(
            repo_id=repo,
            filename=filename,
            repo_type="dataset",
            local_dir=str(local_dir),
            token=token,
        )
    )


def _dirs(name: str, root: str | Path) -> tuple[Path, Path, Path]:
    base = dataset_dir(name, root)
    meta, tasks, dl = base / "meta", base / "tasks", base / "_download"
    for d in (meta, tasks, dl):
        d.mkdir(parents=True, exist_ok=True)
    return meta, tasks, dl


def prepare(
    name: str,
    root: str | Path = "data",
    ids: list[str] | None = None,
    token: str | None = None,
    force: bool = False,
    keep_zips: bool = False,
) -> list[str]:
    """Download problems + data for `name` and extract each task. Returns prepared ids."""
    prepare_meta(name, root, token)
    return prepare_tasks(name, root, ids, token, force=force, keep_zips=keep_zips)


def prepare_meta(name: str, root: str | Path = "data", token: str | None = None) -> None:
    """problems.csv (questions, rubrics, allowed domains) plus the dataset's README."""
    repo = REPOS[name]
    meta, _tasks, dl = _dirs(name, root)
    token = token or os.environ.get("HF_TOKEN") or None
    for fn in META_FILES:
        try:
            src = _hf_download(repo, fn, dl, token)
            shutil.copyfile(src, meta / fn)
        except Exception as e:  # noqa: BLE001 - README/CHANGELOG are optional
            if fn == "problems.csv":
                raise RuntimeError(
                    f"could not download problems.csv from {repo}: {e}\n"
                    + ("(the full set is gated: accept its terms on Hugging Face and set HF_TOKEN)"
                       if name == "full" else "")
                ) from e
            print(f"  note: {fn} not downloaded ({e})")


def prepare_tasks(
    name: str,
    root: str | Path = "data",
    ids: list[str] | None = None,
    token: str | None = None,
    force: bool = False,
    keep_zips: bool = False,
) -> list[str]:
    """Download and extract each task's data (the full set: one zip per task)."""
    from huggingface_hub.errors import EntryNotFoundError, RemoteEntryNotFoundError  # noqa: PLC0415

    repo = REPOS[name]
    _meta, tasks, dl = _dirs(name, root)
    token = token or os.environ.get("HF_TOKEN") or None
    problems = load_problems(name, root)
    wanted = ids or [p.id for p in problems]
    todo = [i for i in wanted if force or not (tasks / i / PREPARED_MARKER).exists()]
    if not todo:
        print(f"{name}: all {len(wanted)} requested tasks already prepared")
        return wanted

    if name == "preview":
        archive = _hf_download(repo, "data.zip", dl, token)
        for i in todo:
            if (tasks / i).exists():
                shutil.rmtree(tasks / i)
        safe_extract(archive, tasks, only_top=set(todo))
        for i in todo:
            if not (tasks / i).is_dir():
                print(f"  warning: {i} has no data in data.zip")
                (tasks / i).mkdir(parents=True, exist_ok=True)
            (tasks / i / PREPARED_MARKER).touch()
            print(f"  prepared {i}")
        if not keep_zips:
            archive.unlink(missing_ok=True)
    else:
        for i in todo:
            target = tasks / i
            if target.exists():
                shutil.rmtree(target)
            target.mkdir(parents=True)
            try:
                archive = _hf_download(repo, f"data/{i}.zip", dl, token)
            except (EntryNotFoundError, RemoteEntryNotFoundError) as e:  # a problem may ship no data
                print(f"  warning: no data archive for {i} ({e})")
                (target / PREPARED_MARKER).touch()
                continue
            safe_extract(archive, target)
            _flatten_single_dir(target)
            (target / PREPARED_MARKER).touch()
            if not keep_zips:
                archive.unlink(missing_ok=True)
            print(f"  prepared {i}")
    return wanted


def dir_size(path: Path) -> int:
    total = 0
    for dirpath, _dirs, files in os.walk(path):
        for fn in files:
            try:
                total += os.lstat(os.path.join(dirpath, fn)).st_size
            except OSError:
                pass
    return total


def list_input_files(path: Path) -> list[str]:
    """Relative paths of the files an agent is handed (marker excluded)."""
    out = []
    for dirpath, _dirs, files in os.walk(path):
        for fn in files:
            if fn == PREPARED_MARKER:
                continue
            out.append(os.path.relpath(os.path.join(dirpath, fn), path))
    return sorted(out)
