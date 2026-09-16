"""Editable prompt and skill registry backed by $PILOT_DATA.

Defaults ship inside the package (pilot_addon/defaults/): prompts/*.md and
skills/<name>/SKILL.md. seed_defaults() copies them into the data dir on
startup, adding files that are missing there without touching owner edits.
The supervisor reads its system prompt and the runtime skill set through
this store, so edits in the workshop take effect on the next daily run.
"""

from __future__ import annotations

import logging
from pathlib import Path
import re
from typing import Any

from .state import RuntimeState

logger = logging.getLogger("pilot.addon")

BUNDLE_DIR = Path(__file__).parent / "defaults"
MAX_CONTENT_BYTES = 64 * 1024
SKILLS_BUDGET_BYTES = 16 * 1024
_NAME_RE = re.compile(r"[a-z0-9_-]{1,64}")


class PromptError(Exception):
    """Invalid name/content or a missing entry; shown as a WS error frame."""


def _validate_name(name: Any) -> str:
    if not isinstance(name, str) or not _NAME_RE.fullmatch(name):
        raise PromptError("name must match [a-z0-9_-]{1,64}")
    return name


def _validate_content(content: Any) -> str:
    if not isinstance(content, str):
        raise PromptError("content must be a string")
    if len(content.encode("utf-8")) > MAX_CONTENT_BYTES:
        raise PromptError(f"content exceeds {MAX_CONTENT_BYTES // 1024} KiB")
    return content


def _data_file(state: RuntimeState, kind: str, name: str) -> Path:
    base = Path(state.data_dir)
    if kind == "prompts":
        return base / "prompts" / f"{name}.md"
    return base / "skills" / name / "SKILL.md"


def _bundle_file(kind: str, name: str) -> Path:
    if kind == "prompts":
        return BUNDLE_DIR / "prompts" / f"{name}.md"
    return BUNDLE_DIR / "skills" / name / "SKILL.md"


def seed_defaults(data_dir: Path) -> None:
    """Copy bundled defaults into data_dir, never overwriting existing files.

    Runs at addon startup: a fresh install gets the whole bundle, an upgraded
    addon picks up newly added default files, and every owner edit survives.
    """
    if not BUNDLE_DIR.is_dir():
        return
    for src in sorted(BUNDLE_DIR.rglob("*")):
        if not src.is_file():
            continue
        dst = data_dir / src.relative_to(BUNDLE_DIR)
        if dst.exists():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(src.read_bytes())
        logger.info("seeded default %s", dst.relative_to(data_dir))


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _parse_frontmatter(text: str) -> dict[str, str]:
    """Minimal `---` frontmatter parser (name/description lines)."""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    meta: dict[str, str] = {}
    for line in text[3:end].strip().splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip():
            meta[key.strip()] = value.strip()
    return meta


def _names(kind: str, state: RuntimeState) -> list[str]:
    names: set[str] = set()
    bundle_root = BUNDLE_DIR / kind
    data_root = Path(state.data_dir) / kind
    if kind == "prompts":
        names |= {p.stem for p in bundle_root.glob("*.md")}
        names |= {p.stem for p in data_root.glob("*.md")}
    else:
        names |= {p.parent.name for p in bundle_root.glob("*/SKILL.md")}
        names |= {p.parent.name for p in data_root.glob("*/SKILL.md")}
    return sorted(names)


def list_entries(state: RuntimeState, kind: str) -> list[dict[str, Any]]:
    """One entry per known name: size, mtime and whether it still matches default."""
    entries: list[dict[str, Any]] = []
    for name in _names(kind, state):
        data_file = _data_file(state, kind, name)
        bundle = _bundle_file(kind, name)
        content = _read(data_file)
        modified_ts: float | None = None
        if content is not None:
            try:
                modified_ts = data_file.stat().st_mtime
            except OSError:
                modified_ts = None
        is_default = content is None or content == _read(bundle)
        entry: dict[str, Any] = {
            "name": name,
            "size": len(content) if content is not None else len(_read(bundle) or ""),
            "modified_ts": modified_ts,
            "is_default": is_default,
        }
        if kind == "skills" and content is not None:
            entry["description"] = _parse_frontmatter(content).get("description", "")
        entries.append(entry)
    return entries


def get_entry(state: RuntimeState, kind: str, name: Any) -> dict[str, Any] | None:
    """The effective content (data dir first, bundle fallback), or None."""
    name = _validate_name(name)
    content = _read(_data_file(state, kind, name))
    if content is None:
        content = _read(_bundle_file(kind, name))
    if content is None:
        return None
    entry: dict[str, Any] = {"name": name, "content": content}
    if kind == "skills":
        entry["description"] = _parse_frontmatter(content).get("description", "")
    return entry


def set_entry(state: RuntimeState, kind: str, name: Any, content: Any) -> str:
    """Write content for name (atomic tmp + rename). Returns the name."""
    clean_name = _validate_name(name)
    text = _validate_content(content)
    dst = _data_file(state, kind, clean_name)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(f"{dst.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(dst)
    return clean_name


def reset_entry(state: RuntimeState, kind: str, name: Any) -> str | None:
    """Restore the bundled default. None when no default exists."""
    clean_name = _validate_name(name)
    bundle = _bundle_file(kind, clean_name)
    if not bundle.is_file():
        return None
    dst = _data_file(state, kind, clean_name)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(bundle.read_bytes())
    return clean_name
