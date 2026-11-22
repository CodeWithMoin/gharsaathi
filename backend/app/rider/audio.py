"""Audio helpers for the call ↔ OpenAI Realtime bridge.

Two plans (chosen from the carrier's ``start`` message, see ``telephony.py``):

* G.711 μ-law 8 kHz both ways: Realtime accepts and produces ``audio/pcmu``, so the
  bytes pass through untouched (no transcoding, no resampling).
* Linear PCM16 (8 or 16 kHz in): resampled to Realtime's 24 kHz PCM; Realtime's
  24 kHz output is played back as L16/24 kHz, which the carrier accepts.
"""

from __future__ import annotations

import array
import sys

REALTIME_PCM_RATE = 24000


def _samples(data: bytes) -> array.array:
    samples = array.array("h")
    samples.frombytes(data[: len(data) - len(data) % 2])
    if sys.byteorder == "big":  # pragma: no cover - little-endian hosts
        samples.byteswap()
    return samples


def _bytes(samples: array.array) -> bytes:
    if sys.byteorder == "big":  # pragma: no cover
        samples = array.array("h", samples)
        samples.byteswap()
    return samples.tobytes()


class Resampler:
    """Streaming linear-interpolation resampler for PCM16 mono.

    Keeps its position and the last input sample between calls, so a stream cut
    into arbitrary chunks resamples to the same samples as one long buffer.
    """

    def __init__(self, source_rate: int, target_rate: int) -> None:
        if source_rate <= 0 or target_rate <= 0:
            raise ValueError("Sample rates must be positive.")
        self.source_rate = source_rate
        self.target_rate = target_rate
        self._step = source_rate / target_rate
        self._position = 0.0
        self._previous: int | None = None
        self._odd = b""

    def process(self, data: bytes) -> bytes:
        data = self._odd + data
        self._odd = data[len(data) - len(data) % 2 :]
        if self.source_rate == self.target_rate:
            return data[: len(data) - len(data) % 2]
        incoming = _samples(data)
        if not incoming:
            return b""
        # Index 0 is the last sample of the previous chunk (or the first sample).
        if self._previous is None:
            window = incoming
        else:
            window = array.array("h", [self._previous])
            window.extend(incoming)
        out = array.array("h")
        position = self._position
        last = len(window) - 1
        while position < last:
            index = int(position)
            fraction = position - index
            a = window[index]
            b = window[index + 1]
            out.append(int(round(a + (b - a) * fraction)))
            position += self._step
        if self._previous is None and last == 0:
            out.append(window[0])
            position = self._step
        self._position = position - last
        self._previous = window[last]
        return _bytes(out)


class OutboundChunker:
    """Cuts outgoing audio into fixed frames (``push``); ``flush`` sends the rest
    whole-sample aligned; ``drop`` discards it (barge-in)."""

    def __init__(self, frame_bytes: int, sample_bytes: int = 1) -> None:
        if frame_bytes <= 0 or frame_bytes % sample_bytes:
            raise ValueError("Frame size must be a positive multiple of the sample size.")
        self.frame_bytes = frame_bytes
        self.sample_bytes = sample_bytes
        self._buffer = bytearray()

    def push(self, data: bytes) -> list[bytes]:
        self._buffer.extend(data)
        chunks: list[bytes] = []
        while len(self._buffer) >= self.frame_bytes:
            chunks.append(bytes(self._buffer[: self.frame_bytes]))
            del self._buffer[: self.frame_bytes]
        return chunks

    def flush(self) -> list[bytes]:
        usable = len(self._buffer) - len(self._buffer) % self.sample_bytes
        chunk = bytes(self._buffer[:usable])
        self._buffer.clear()
        return [chunk] if chunk else []

    def drop(self) -> None:
        self._buffer.clear()

    @property
    def pending(self) -> int:
        return len(self._buffer)
