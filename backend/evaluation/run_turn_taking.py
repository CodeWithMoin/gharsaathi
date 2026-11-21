"""Turn-taking evaluation for GPT-Live voice (docs/voice-turn-taking.md).

Builds Hinglish clips with macOS ``say`` + ffmpeg (mid-sentence pauses after
fillers, complete short requests, and barge-in over assistant speech), then:

- offline: runs the browser turn gate + smart-turn ONNX model on every clip via
  ``frontend/scripts/eval-smart-turn.mjs`` (option B), plus energy-silence
  endpointers as reference proxies;
- ``--live``: streams a handful of clips to GPT-Live over the primary WebSocket
  for the ``native`` and ``tuned`` (option A) instructions. No tools and no
  commerce calls; client delegation results are never returned.

Run from backend/:
    uv run --with-requirements requirements.txt python -m evaluation.run_turn_taking [--live]
"""

import argparse
import array
import asyncio
import base64
import json
import math
import random
import subprocess
import sys
import tempfile
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CLIPS = Path(__file__).resolve().parent / ".turn_taking_clips"
REPORT = Path(__file__).resolve().parent / "turn_taking_report.json"
USER_RMS = 0.06
ASSISTANT_RMS = 0.1
NOISE_RMS = 0.002


@dataclass(frozen=True)
class Utterance:
    id: str
    # Alternating text and pause seconds; romanised for Rishi/Aman, Devanagari for Lekha.
    roman: tuple[str | float, ...]
    devanagari: tuple[str | float, ...]
    # Per pause: "hesitation" (after a filler or unfinished phrase) or "clause"
    # (after a phrase that is complete on its own, then an afterthought).
    pause_kinds: tuple[str, ...] = ()


PAUSED = (
    Utterance("axe_dark", ("Mujhe AXE deo chahiye", 0.9, "haan, woh", 1.2, "Dark Temptation wala"),
              ("मुझे AXE deo चाहिए", 0.9, "हाँ, वो", 1.2, "Dark Temptation वाला"), ("clause", "hesitation")),
    Utterance("oil_atta", ("Ek litre Fortune oil", 1.0, "aur", 0.8, "do packet Aashirvaad atta bhi"),
              ("एक लीटर Fortune तेल", 1.0, "और", 0.8, "दो पैकेट आशीर्वाद आटा भी"), ("clause", "hesitation")),
    Utterance("amul_milk", ("Amul doodh add karo", 0.7, "umm, matlab", 1.0, "do packet"),
              ("अमूल दूध ऐड करो", 0.7, "उम्म, मतलब", 1.0, "दो पैकेट"), ("clause", "hesitation")),
    Utterance("surf_wait", ("Mujhe chahiye", 1.4, "ek minute", 0.6, "haan, Surf Excel ka bada pack"),
              ("मुझे चाहिए", 1.4, "एक मिनट", 0.6, "हाँ, Surf Excel का बड़ा पैक"), ("hesitation", "hesitation")),
    Utterance("store_choice", ("Instamart pe dekho", 1.0, "ya phir", 0.9, "Zepto pe, jo bhi jaldi aaye"),
              ("Instamart पे देखो", 1.0, "या फिर", 0.9, "Zepto पे, जो भी जल्दी आए"), ("clause", "hesitation")),
    Utterance("maggi_recall", ("Woh jo pichli baar liya tha", 1.2, "kya naam tha", 1.0, "haan, Maggi masala"),
              ("वो जो पिछली बार लिया था", 1.2, "क्या नाम था", 1.0, "हाँ, Maggi मसाला"), ("hesitation", "hesitation")),
)
COMPLETE = (
    Utterance("onion", ("Ek kilo pyaaz add kar do.",), ("एक किलो प्याज़ ऐड कर दो।",)),
    Utterance("show_basket", ("Bas itna hi, basket dikhao.",), ("बस इतना ही, बास्केट दिखाओ।",)),
    Utterance("dettol", ("Dettol handwash ka refill chahiye.",), ("Dettol handwash का refill चाहिए।",)),
    Utterance("total", ("Kitne ka padega total?",), ("कितने का पड़ेगा टोटल?",)),
)
ASSISTANT_TEXT = (
    "Fortune Sunlite refined oil ek litre ka pack ek sau pachpan rupaye ka hai. Saffola Gold thoda "
    "mehenga hai, do sau das rupaye. Dhara ka pack sabse sasta hai, aur dono stores par das minute "
    "mein delivery ho jayegi."
)
INTERRUPTION = "Ruko ruko, Dark wala chahiye."
USER_VOICES = (("Rishi", "roman"), ("Lekha", "devanagari"), ("Aman", "roman"))
ECHO_DB = (-30, -20, -12)


def _tts(text: str, voice: str, rate: int) -> array.array:
    with tempfile.TemporaryDirectory() as directory:
        aiff = Path(directory) / "speech.aiff"
        wav = Path(directory) / "speech.wav"
        subprocess.run(["say", "-v", voice, "-o", str(aiff), text], check=True)
        subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-y", "-i", str(aiff), "-ac", "1", "-ar", str(rate), "-c:a", "pcm_s16le", str(wav)],
            check=True,
        )
        with wave.open(str(wav)) as handle:
            samples = array.array("h", handle.readframes(handle.getnframes()))
    return _trim(array.array("f", (sample / 32768 for sample in samples)), rate)


def _trim(samples: array.array, rate: int, threshold: float = 0.01) -> array.array:
    frame = rate // 50
    energetic = [
        index for index in range(0, len(samples), frame)
        if _rms(samples[index:index + frame]) >= threshold
    ]
    if not energetic:
        return samples
    return samples[energetic[0]:min(len(samples), energetic[-1] + frame)]


def _rms(samples: Any) -> float:
    return math.sqrt(sum(value * value for value in samples) / len(samples)) if len(samples) else 0.0


def _scale(samples: array.array, target_rms: float) -> array.array:
    level = _rms(samples) or 1.0
    return array.array("f", (max(-1.0, min(1.0, value * target_rms / level)) for value in samples))


def _assemble(parts: tuple[str | float, ...], voice: str, rate: int, lead: float = 0.5, tail: float = 3.0) -> tuple[array.array, float, list[list[float]]]:
    """Returns the mic signal, true speech end (s) and pause ranges."""
    signal = array.array("f", [0.0] * int(lead * rate))
    pauses: list[list[float]] = []
    for part in parts:
        if isinstance(part, float):
            start = len(signal) / rate
            signal.extend([0.0] * int(part * rate))
            pauses.append([round(start, 3), round(len(signal) / rate, 3)])
        else:
            signal.extend(_scale(_tts(part, voice, rate), USER_RMS))
    speech_end = len(signal) / rate
    signal.extend([0.0] * int(tail * rate))
    noise = random.Random(7)
    return array.array("f", (value + noise.gauss(0, NOISE_RMS) for value in signal)), round(speech_end, 3), pauses


def _write(path: Path, samples: array.array, rate: int) -> None:
    pcm = array.array("h", (int(max(-1.0, min(1.0, value)) * 32767) for value in samples))
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(pcm.tobytes())


def build_clips() -> dict[str, Any]:
    CLIPS.mkdir(exist_ok=True)
    clips: list[dict[str, Any]] = []
    for utterance in PAUSED + COMPLETE:
        kind = "pause" if utterance in PAUSED else "complete"
        for voice, script in USER_VOICES:
            parts = utterance.devanagari if script == "devanagari" else utterance.roman
            clip_id = f"{utterance.id}-{voice.lower()}"
            entry: dict[str, Any] = {"id": clip_id, "kind": kind, "voice": voice, "text": " … ".join(p for p in parts if isinstance(p, str))}
            for rate in (16_000, 24_000):
                signal, speech_end, pauses = _assemble(parts, voice, rate)
                path = CLIPS / f"{clip_id}-{rate // 1000}k.wav"
                _write(path, signal, rate)
                entry["mic" if rate == 16_000 else "mic_24k"] = str(path)
                entry["speech_end_s"], entry["pauses"] = speech_end, pauses
            entry["pause_kinds"] = list(utterance.pause_kinds)
            clips.append(entry)

    for rate in (16_000, 24_000):
        assistant = _scale(_tts(ASSISTANT_TEXT, "Lekha", rate), ASSISTANT_RMS)
        _write(CLIPS / f"assistant-{rate // 1000}k.wav", assistant, rate)
        interruption = _scale(_tts(INTERRUPTION, "Rishi", rate), USER_RMS)
        _write(CLIPS / f"interruption-{rate // 1000}k.wav", interruption, rate)
    for echo_db in ECHO_DB:
        base = {"assistant": str(CLIPS / "assistant-16k.wav"), "echo_db": echo_db}
        clips.append({"id": f"barge_in-echo{echo_db}", "kind": "barge_in", "user": str(CLIPS / "interruption-16k.wav"), "user_start_s": 2.0, **base})
        clips.append({"id": f"echo_only-echo{echo_db}", "kind": "echo_only", **base})
    manifest = {"sample_rate": 16_000, "clips": clips}
    (CLIPS / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    return manifest


def run_offline() -> dict[str, Any]:
    out = CLIPS / "offline.json"
    subprocess.run(
        ["node", "scripts/eval-smart-turn.mjs", str(CLIPS / "manifest.json"), str(out)],
        cwd=ROOT / "frontend", check=True, stdout=subprocess.DEVNULL,
    )
    return json.loads(out.read_text())


# ---------------------------------------------------------------- live (GPT-Live)

CHUNK_S = 0.04
SPEECH_FRAME_RMS = 0.02


class OutputTimeline:
    """Simulates a local player for output deltas and finds speech in wall time."""

    def __init__(self, t0: float) -> None:
        self.t0 = t0
        self.cursor = 0.0
        self.frames: list[tuple[float, float]] = []  # (wall seconds since t0, rms) per 20 ms

    def add(self, pcm: bytes, arrived: float) -> None:
        samples = array.array("h")
        samples.frombytes(pcm)
        start = max(arrived - self.t0, self.cursor)
        frame = 480
        for index in range(0, len(samples), frame):
            chunk = samples[index:index + frame]
            self.frames.append((start + index / 24_000, _rms([value / 32768 for value in chunk])))
        self.cursor = start + len(samples) / 24_000

    def onset_after(self, after: float) -> float | None:
        run = 0
        for at, level in self.frames:
            if at < after:
                continue
            run = run + 1 if level >= SPEECH_FRAME_RMS else 0
            if run >= 3:
                return at - 0.04
        return None

    def speech_until(self, after: float, gap: float = 0.4) -> float | None:
        """End of the speech that is playing at `after` (first silence gap >= `gap`)."""
        last_speech = None
        for at, level in self.frames:
            if at < after:
                continue
            if level >= SPEECH_FRAME_RMS:
                last_speech = at + 0.02
            elif last_speech is not None and at - last_speech >= gap:
                return last_speech
            elif last_speech is None and at - after >= gap:
                return after
        return last_speech


async def _live_session(settings: Any, instructions: str, plan: list[tuple[str, Any]]) -> dict[str, Any]:
    from websockets.asyncio.client import connect

    url = settings.openai_base_url.replace("https://", "wss://").rstrip("/") + "/live/sessions"
    headers = {"Authorization": f"Bearer {settings.openai_api_key.get_secret_value()}"}
    transcript: list[str] = []
    user_transcript: list[str] = []
    async with connect(url, additional_headers=headers, max_size=None) as socket:
        await socket.send(json.dumps({
            "type": "session.start",
            "session": {
                "model": settings.live_model,
                "instructions": instructions,
                "audio": {"format": {"type": "audio/pcm", "rate": 24_000}, "output": {"voice": settings.live_voice}},
                "delegation": {"type": "client"},
            },
        }))
        while True:
            event = json.loads(await socket.recv())
            if event.get("type") == "session.started":
                break
            if event.get("type") == "error":
                raise RuntimeError(event.get("error", {}).get("message", "Live session failed"))
        t0 = time.monotonic()
        timeline = OutputTimeline(t0)
        marks: dict[str, float] = {}

        async def receive() -> None:
            async for raw in socket:
                event = json.loads(raw)
                kind = event.get("type")
                if kind == "session.output_audio.delta":
                    timeline.add(base64.b64decode(event["delta"]), time.monotonic())
                elif kind == "session.output_transcript.delta":
                    transcript.append(event.get("delta", ""))
                elif kind == "session.input_transcript.delta":
                    user_transcript.append(event.get("delta", ""))
                elif kind == "session.closed":
                    return

        receiver = asyncio.create_task(receive())
        sent = 0.0
        silence = array.array("h", [0] * int(24_000 * CHUNK_S)).tobytes()

        async def stream(pcm: bytes) -> None:
            nonlocal sent
            step = int(24_000 * CHUNK_S) * 2
            for index in range(0, len(pcm), step):
                await socket.send(json.dumps({"type": "session.input_audio.append", "audio": base64.b64encode(pcm[index:index + step]).decode()}))
                sent += CHUNK_S
                await asyncio.sleep(max(0.0, t0 + sent - time.monotonic()))

        for action, value in plan:
            if action == "audio":
                await stream(value)
            elif action == "silence":
                for _ in range(int(value / CHUNK_S)):
                    await stream(silence)
            elif action == "mark":
                marks[value] = sent
            elif action == "until_speaking":
                # Keep streaming silence until assistant speech has played for `value` seconds.
                deadline = sent + 12
                while sent < deadline:
                    onset = timeline.onset_after(marks.get("request_end", 0))
                    if onset is not None and sent >= onset + value:
                        break
                    await stream(silence)
                marks["interrupt"] = sent
        await socket.send(json.dumps({"type": "session.close"}))
        try:
            await asyncio.wait_for(receiver, 10)
        except TimeoutError:
            receiver.cancel()
    return {"timeline": timeline, "marks": marks, "assistant": "".join(transcript).strip(), "user": "".join(user_transcript).strip()}


def _pcm24(path: str) -> bytes:
    with wave.open(path) as handle:
        return handle.readframes(handle.getnframes())


async def run_live(manifest: dict[str, Any], cases: set[str], interrupt_after: float) -> list[dict[str, Any]]:
    sys.path.insert(0, str(ROOT / "backend"))
    from app.core.config import Settings
    from app.voice.prompts import LIVE_INSTRUCTIONS
    from app.voice.turn_taking import live_instructions

    settings = Settings()
    if not settings.openai_api_key.get_secret_value().strip():
        raise SystemExit("OPENAI_API_KEY is not set; skip --live.")
    by_id = {clip["id"]: clip for clip in manifest["clips"]}
    eot_ids = ["axe_dark-rishi", "surf_wait-lekha", "maggi_recall-aman"]
    rows: list[dict[str, Any]] = []
    for mode in ("native", "tuned"):
        instructions = live_instructions(LIVE_INSTRUCTIONS, mode)  # type: ignore[arg-type]
        for clip_id in eot_ids if "eot" in cases else ():
            clip = by_id[clip_id]
            result = await _live_session(settings, instructions, [("audio", _pcm24(clip["mic_24k"])), ("silence", 5.0)])
            onset = result["timeline"].onset_after(0.4)
            end = clip["speech_end_s"]
            cut_off = onset is not None and onset < end - 0.05
            rows.append({
                "mode": mode, "kind": "pause", "id": clip_id,
                "assistant_onset_s": None if onset is None else round(onset, 3),
                "speech_end_s": end, "pauses": clip["pauses"], "cut_off": cut_off,
                "latency_ms": None if onset is None or cut_off else round((onset - end) * 1000),
                "assistant": result["assistant"], "user_transcript": result["user"],
            })
            print(json.dumps(rows[-1], ensure_ascii=False))
        if "barge" not in cases:
            continue
        request = by_id["dettol-rishi"]
        result = await _live_session(settings, instructions, [
            ("audio", _pcm24(request["mic_24k"])[: int(request["speech_end_s"] * 24_000) * 2]),
            ("mark", "request_end"),
            ("until_speaking", interrupt_after),
            ("audio", _pcm24(str(CLIPS / "interruption-24k.wav"))),
            ("silence", 5.0),
        ])
        interrupt = result["marks"].get("interrupt")
        onset = result["timeline"].onset_after(result["marks"].get("request_end", 0))
        speech_end = result["timeline"].speech_until(interrupt) if interrupt is not None and onset is not None and onset < interrupt else None
        rows.append({
            "mode": mode, "kind": "barge_in", "id": "dettol-rishi+interruption",
            "assistant_onset_s": None if onset is None else round(onset, 3),
            "interrupt_after_s": interrupt_after,
            "interrupt_s": None if interrupt is None else round(interrupt, 3),
            "spoke_over_ms": None if speech_end is None else round((speech_end - interrupt) * 1000),
            "assistant": result["assistant"], "user_transcript": result["user"],
        })
        print(json.dumps(rows[-1], ensure_ascii=False))
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="also run a handful of GPT-Live sessions (uses OPENAI_API_KEY)")
    parser.add_argument("--reuse-clips", action="store_true")
    parser.add_argument("--live-cases", default="eot,barge", help="comma list: eot,barge")
    parser.add_argument("--interrupt-after", type=float, default=1.0, help="seconds of assistant speech before barge-in")
    args = parser.parse_args()
    manifest = json.loads((CLIPS / "manifest.json").read_text()) if args.reuse_clips else build_clips()
    report: dict[str, Any] = {"offline": run_offline()}
    previous = json.loads(REPORT.read_text()).get("live", []) if REPORT.exists() else []
    report["live"] = previous
    if args.live:
        # Appends: live runs cost money, so earlier runs are kept in the report.
        report["live"] = previous + asyncio.run(run_live(manifest, set(args.live_cases.split(",")), args.interrupt_after))
    REPORT.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    offline = report["offline"]
    print(json.dumps({
        "variants": {name: value["summary"] for name, value in offline["variants"].items()},
        "baselines": {name: value["summary"] for name, value in offline["baselines"].items()},
        "barge_in": offline["barge_in"],
        "model": offline["model"],
    }, indent=2))


if __name__ == "__main__":
    main()
