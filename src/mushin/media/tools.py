from __future__ import annotations

import math
import re
import shutil
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from mushin.exceptions import MediaToolError


@dataclass(frozen=True)
class ToolConfig:
    """Executable names searched on PATH, or explicit paths; no shell syntax."""

    ffmpeg: str | Path = "ffmpeg"
    ffprobe: str | Path = "ffprobe"
    timeout: float = 10.0

    def __post_init__(self) -> None:
        if not math.isfinite(self.timeout) or self.timeout <= 0:
            raise ValueError("Tool discovery timeout must be finite and positive")


@dataclass(frozen=True)
class ToolExecutable:
    path: Path
    version: str


@dataclass(frozen=True)
class MediaTools:
    """An immutable snapshot of installed tool versions and advertised support.

    Discovery is synchronous startup work, bounded by a timeout per command.
    These inventories do not guarantee that a codec/container combination or
    hardware encoder works for a particular input. Conversion verifies that
    separately. Stream copying requires no encoder or decoder.
    """

    ffmpeg: ToolExecutable
    ffprobe: ToolExecutable
    muxers: frozenset[str]
    demuxers: frozenset[str]
    encoders: frozenset[str]
    decoders: frozenset[str]

    @classmethod
    def discover(cls, config: ToolConfig | None = None) -> MediaTools:
        config = config or ToolConfig()
        # Resolve both first, so a missing ffprobe fails before launching tools.
        ffmpeg_path = _resolve(config.ffmpeg)
        ffprobe_path = _resolve(config.ffprobe)
        ffmpeg = _version(ffmpeg_path, "ffmpeg", config.timeout)
        ffprobe = _version(ffprobe_path, "ffprobe", config.timeout)
        inventories = {}
        for kind in ("muxers", "demuxers", "encoders", "decoders"):
            output = _query(ffmpeg_path, f"-{kind}", config.timeout)
            inventories[kind] = _inventory(output, kind, ffmpeg_path)
        return cls(ffmpeg=ffmpeg, ffprobe=ffprobe, **inventories)

    def require(
        self,
        *,
        muxers: Iterable[str] = (),
        demuxers: Iterable[str] = (),
        encoders: Iterable[str] = (),
        decoders: Iterable[str] = (),
    ) -> None:
        """Check exact FFmpeg names, reporting all missing capabilities."""
        missing = []
        for kind, requested in (("muxers", muxers), ("demuxers", demuxers), ("encoders", encoders), ("decoders", decoders)):
            names = set(requested) - getattr(self, kind)
            if names:
                missing.append(f"{kind}: {', '.join(sorted(names))}")
        if missing:
            raise MediaToolError("missing_capability", "FFmpeg lacks required " + "; ".join(missing), executable=str(self.ffmpeg.path))


def _resolve(value: str | Path) -> Path:
    path = shutil.which(str(value))
    if path is None:
        raise MediaToolError(
            "missing_executable", f"Cannot find executable {str(value)!r}; install it or configure its executable path.", executable=str(value)
        )
    return Path(path).absolute()


def _query(path: Path, option: str, timeout: float) -> str:
    try:
        result = subprocess.run(
            [str(path), "-hide_banner", option],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise MediaToolError("discovery_timeout", f"{path} {option} exceeded {timeout}s", executable=str(path)) from exc
    except OSError as exc:
        raise MediaToolError("launch_failed", f"Cannot launch {path}: {exc}", executable=str(path)) from exc
    if result.returncode:
        diagnostic = result.stderr[-4096:].decode("utf-8", errors="replace")
        raise MediaToolError("command_failed", f"{path} {option} exited with {result.returncode}: {diagnostic}", executable=str(path))
    return result.stdout.decode("utf-8", errors="replace")


def _version(path: Path, name: str, timeout: float) -> ToolExecutable:
    output = _query(path, "-version", timeout)
    first = output.splitlines()[0] if output.splitlines() else ""
    if not first.startswith(f"{name} version ") or not first[len(name) + 9 :].strip():
        raise MediaToolError("invalid_tool_output", f"{path} did not report a valid {name} version", executable=str(path))
    return ToolExecutable(path=path, version=first)


def _inventory(output: str, kind: str, path: Path) -> frozenset[str]:
    # Ignore legend rows (whose name is '='); support comma-separated aliases
    # and the optional device flag in newer FFmpeg format listings.
    flags = r"[DEd.]{1,3}" if kind in ("muxers", "demuxers") else r"[VAS.][A-Z.]{5}"
    pattern = re.compile(rf"^\s+{flags}\s+([a-zA-Z0-9_][a-zA-Z0-9_,.-]*)\s+\S", re.MULTILINE)
    names = frozenset(name for match in pattern.finditer(output) for name in match[1].split(","))
    heading = "Formats:" if kind in ("muxers", "demuxers") else f"{kind.capitalize()}:"
    if heading not in output:
        raise MediaToolError("invalid_tool_output", f"{path} returned invalid {kind} output", executable=str(path))
    return names
