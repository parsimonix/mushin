"""Bounded, incremental parsing of FFmpeg's machine-readable progress stream."""
from collections.abc import Callable
from time import monotonic
import math


class FFmpegProgressParser:
    """Emit media seconds at most every interval, always including the end record.

    The end record describes FFmpeg processing, not verification or publication.
    Invalid values are ignored; media time never moves backwards.
    """

    def __init__(self, callback: Callable[[float], None], *, interval: float = 0.25,
                 clock: Callable[[], float] = monotonic) -> None:
        if not math.isfinite(interval) or interval < 0:
            raise ValueError("interval must be finite and nonnegative")
        self.callback = callback
        self.interval = interval
        self.clock = clock
        self.buffer = bytearray()
        self.discard_line = False
        self.seconds: float | None = None
        self.last_seconds = 0.0
        self.last_emit = float('-inf')
        self.ended = False

    def feed(self, chunk: bytes) -> None:
        for part in chunk.splitlines(keepends=True):
            if not self.discard_line:
                self.buffer.extend(part)
                if len(self.buffer) > 4096:
                    self.buffer.clear()
                    self.discard_line = True
            if part.endswith(b'\n'):
                if not self.discard_line:
                    self._line(bytes(self.buffer).strip())
                self.buffer.clear()
                self.discard_line = False

    def _line(self, line: bytes) -> None:
        if self.ended:
            return
        key, _, value = line.partition(b'=')
        if key in (b'out_time_us', b'out_time_ms'):
            # FFmpeg's historical out_time_ms field also contains microseconds.
            try:
                seconds = float(value) / 1_000_000
            except ValueError:
                return
            if math.isfinite(seconds) and seconds >= 0:
                self.seconds = seconds
        elif key == b'progress':
            end = value == b'end'
            now = self.clock()
            if self.seconds is not None and (end or now - self.last_emit >= self.interval):
                self.last_seconds = max(self.last_seconds, self.seconds)
                self.callback(self.last_seconds)
                self.last_emit = now
            self.seconds = None
            self.ended = end
