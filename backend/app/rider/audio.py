"""PCM16 mono audio helpers for the Exotel ↔ OpenAI Realtime bridge.

Exotel streams raw 16-bit little-endian mono PCM at 8, 16 or 24 kHz; Realtime's
PCM format is 24 kHz. Audio we send to Exotel must arrive in chunks of
3,200–100,000 bytes that are a multiple of 320 bytes
(https://developer.exotel.com/docs/agentstream/stream-voicebot-applet).
"""

from __future__ import annotations

import array
import sys

SUPPORTED_RATES = (8000, 16000, 24000)
REALTIME_RATE = 24000
CHUNK_MULTIPLE = 320
MIN_CHUNK_BYTES = 3200
MAX_CHUNK_BYTES = 100_000
# Send roughly 100 ms per media message at 8 kHz, more at higher rates: small enough
# for a responsive `clear` (barge-in), never below Exotel's minimum.
TARGET_CHUNK_MS = 100


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
        self._position = 0.0  # next output position, in input samples, relative to _previous
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
            # A single first sample: emit it and continue from there.
            out.append(window[0])
            position = self._step
        self._position = position - last
        self._previous = window[last]
        return _bytes(out)

    def reset(self) -> None:
        self._position = 0.0
        self._previous = None
        self._odd = b""


def silence(byte_count: int) -> bytes:
    return b"\x00" * byte_count


def chunk_bytes_for(rate: int) -> int:
    """Bytes in TARGET_CHUNK_MS at ``rate``, rounded to Exotel's rules."""

    size = rate * 2 * TARGET_CHUNK_MS // 1000
    size -= size % CHUNK_MULTIPLE
    return max(MIN_CHUNK_BYTES, min(MAX_CHUNK_BYTES, size))


def valid_chunk(size: int) -> bool:
    return MIN_CHUNK_BYTES <= size <= MAX_CHUNK_BYTES and size % CHUNK_MULTIPLE == 0


class OutboundChunker:
    """Cuts outgoing PCM into Exotel-valid chunks.

    ``push`` returns only full chunks; ``flush`` pads the remainder with silence
    up to a valid size (end of an assistant turn); ``drop`` discards it (barge-in).
    """

    def __init__(self, rate: int) -> None:
        if rate not in SUPPORTED_RATES:
            raise ValueError(f"Unsupported Exotel sample rate {rate}.")
        self.rate = rate
        self.chunk_size = chunk_bytes_for(rate)
        self._buffer = bytearray()

    def push(self, data: bytes) -> list[bytes]:
        self._buffer.extend(data)
        chunks: list[bytes] = []
        while len(self._buffer) >= self.chunk_size:
            chunks.append(bytes(self._buffer[: self.chunk_size]))
            del self._buffer[: self.chunk_size]
        return chunks

    def flush(self) -> list[bytes]:
        if not self._buffer:
            return []
        size = len(self._buffer)
        padded = max(MIN_CHUNK_BYTES, size + (-size % CHUNK_MULTIPLE))
        chunk = bytes(self._buffer) + silence(padded - size)
        self._buffer.clear()
        return [chunk]

    def drop(self) -> None:
        self._buffer.clear()

    @property
    def pending(self) -> int:
        return len(self._buffer)


def duration_ms(byte_count: int, rate: int) -> int:
    return byte_count * 1000 // (rate * 2)
