"""Resolve the self-play workspace from source location and enforce write bounds."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Union

from .errors import ProtocolError, ViolationCode

PROTOCOL_VERSION = "sp-protocol-v1"
PathLike = Union[str, Path]


class WorkspaceBoundaryError(ProtocolError):
    def __init__(self, message: str, details: Optional[dict] = None) -> None:
        super().__init__(ViolationCode.WORKSPACE_BOUNDARY, message, details)


def _as_path(value: PathLike) -> Path:
    return value if isinstance(value, Path) else Path(value)


def is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


@dataclass(frozen=True)
class Workspace:
    """All Self-Play writes must stay under self_play_root."""

    self_play_root: Path
    data_root: Path
    cope_alias_root: Path
    pog_root: Path

    @classmethod
    def from_this_package(cls) -> "Workspace":
        # src/sp_memory/paths.py -> self-play/
        self_play_root = Path(__file__).resolve().parents[2]
        clue_root = self_play_root.parent
        return cls(
            self_play_root=self_play_root,
            data_root=clue_root / "data",
            cope_alias_root=clue_root / "cope_alias",
            pog_root=clue_root / "PoG",
        )

    @classmethod
    def for_tests(
        cls,
        self_play_root: PathLike,
        data_root: Optional[PathLike] = None,
        cope_alias_root: Optional[PathLike] = None,
        pog_root: Optional[PathLike] = None,
    ) -> "Workspace":
        root = _as_path(self_play_root).resolve()
        parent = root.parent
        return cls(
            self_play_root=root,
            data_root=_as_path(data_root).resolve() if data_root else parent / "data",
            cope_alias_root=_as_path(cope_alias_root).resolve()
            if cope_alias_root
            else parent / "cope_alias",
            pog_root=_as_path(pog_root).resolve() if pog_root else parent / "PoG",
        )

    @property
    def clue_on_graph_root(self) -> Path:
        return self.self_play_root.parent

    @property
    def src_root(self) -> Path:
        return self.self_play_root / "src"

    @property
    def configs_root(self) -> Path:
        return self.self_play_root / "configs"

    @property
    def artifacts_root(self) -> Path:
        return self.self_play_root / "artifacts"

    @property
    def runs_root(self) -> Path:
        return self.self_play_root / "runs"

    @property
    def logs_root(self) -> Path:
        return self.self_play_root / "logs"

    @property
    def reports_root(self) -> Path:
        return self.self_play_root / "reports"

    @property
    def tests_root(self) -> Path:
        return self.self_play_root / "tests"

    def read_only_roots(self) -> List[Path]:
        return [self.data_root, self.cope_alias_root, self.pog_root]

    def output_roots(self) -> List[Path]:
        return [
            self.configs_root,
            self.artifacts_root,
            self.runs_root,
            self.logs_root,
            self.reports_root,
        ]

    def ensure_output_dirs(self) -> None:
        for path in self.output_roots():
            self.assert_writable(path)
            path.mkdir(parents=True, exist_ok=True)

    def _symlink_escape(self, path: Path) -> Optional[Path]:
        """Return the first symlink in the existing prefix that resolves outside self-play."""
        parts = path.parts
        if not parts:
            return None
        acc = Path(parts[0])
        for part in parts[1:]:
            acc = acc / part
            if acc.exists() and acc.is_symlink():
                resolved = acc.resolve()
                root = self.self_play_root.resolve()
                if resolved != root and not is_relative_to(resolved, root):
                    return acc
        return None

    def resolve_candidate(self, path: PathLike, *, relative_to_self_play: bool = True) -> Path:
        candidate = _as_path(path)
        if not candidate.is_absolute():
            if relative_to_self_play:
                candidate = self.self_play_root / candidate
            else:
                candidate = Path.cwd() / candidate
        return candidate

    def assert_writable(self, path: PathLike) -> Path:
        candidate = self.resolve_candidate(path)
        text = str(candidate)
        if "\x00" in text:
            raise WorkspaceBoundaryError("null byte in path", {"path": text})

        escaped = self._symlink_escape(candidate)
        if escaped is not None:
            raise WorkspaceBoundaryError(
                "symlink escapes self-play write root",
                {"path": str(candidate), "symlink": str(escaped)},
            )

        try:
            resolved = candidate.resolve()
        except OSError as exc:
            raise WorkspaceBoundaryError(
                "cannot resolve path",
                {"path": str(candidate), "error": str(exc)},
            ) from exc

        root = self.self_play_root.resolve()
        if resolved != root and not is_relative_to(resolved, root):
            raise WorkspaceBoundaryError(
                "write path is outside self-play/",
                {"path": str(candidate), "resolved": str(resolved), "root": str(root)},
            )

        for forbidden_name, forbidden in (
            ("data", self.data_root),
            ("cope_alias", self.cope_alias_root),
            ("PoG", self.pog_root),
        ):
            forbidden_resolved = forbidden.resolve()
            if resolved == forbidden_resolved or is_relative_to(resolved, forbidden_resolved):
                raise WorkspaceBoundaryError(
                    f"refusing write into read-only {forbidden_name}/",
                    {"path": str(resolved), "forbidden_root": str(forbidden_resolved)},
                )
        return resolved

    def assert_readable_input(self, path: PathLike, *, root: Optional[Path] = None) -> Path:
        candidate = _as_path(path)
        if not candidate.is_absolute():
            if root is None:
                raise WorkspaceBoundaryError(
                    "relative input path requires an explicit root",
                    {"path": str(candidate)},
                )
            candidate = root / candidate
        resolved = candidate.resolve()
        allowed = [self.data_root.resolve(), self.cope_alias_root.resolve()]
        if not any(resolved == item or is_relative_to(resolved, item) for item in allowed):
            raise WorkspaceBoundaryError(
                "input path is not under data/ or cope_alias/",
                {"path": str(resolved)},
            )
        return resolved

    def safe_write_text(self, path: PathLike, text: str, *, encoding: str = "utf-8") -> Path:
        resolved = self.assert_writable(path)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(text, encoding=encoding)
        return resolved

    def safe_write_bytes(self, path: PathLike, data: bytes) -> Path:
        resolved = self.assert_writable(path)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_bytes(data)
        return resolved

    def reject_write_samples(self, samples: Sequence[PathLike]) -> List[dict]:
        results = []
        for sample in samples:
            try:
                self.assert_writable(sample)
                results.append({"path": str(sample), "rejected": False})
            except WorkspaceBoundaryError as exc:
                results.append(
                    {
                        "path": str(sample),
                        "rejected": True,
                        "code": exc.code.value,
                        "message": exc.message,
                    }
                )
        return results
