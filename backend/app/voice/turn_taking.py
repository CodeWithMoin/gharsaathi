"""Turn-taking modes for GPT-Live voice sessions.

GPT-Live (``gpt-live-1.1``) is full duplex and decides when to speak and when to
yield on its own. Its session schema has no ``turn_detection``, VAD threshold,
eagerness, ``create_response``/``interrupt_response``, input commit or
response-cancel control (see docs/voice-turn-taking.md). The documented levers
are the Live instructions (interruption and patience policy), local playback
control in the client, and ``session.instructions.append``, which can interrupt
speech in progress. The modes below only use those levers.

- ``native``: current behaviour, no extra policy.
- ``tuned`` (option A): adds an interruption and patience policy for Hinglish
  hesitations to the Live instructions.
- ``smart_turn`` (option B): ``tuned`` on the server, plus a browser-side
  Hinglish end-of-turn model that holds assistant playback while the user is
  mid-thought and stops playback on barge-in. If the browser cannot load the
  model it keeps the ``tuned`` session, so the fallback is option A.
"""

from typing import Literal

TurnTakingMode = Literal["native", "tuned", "smart_turn"]

TURN_TAKING_POLICY = """\

# Turn-taking
Interruption policy: When the user starts speaking while you are talking, stop
speaking right away, even in the middle of a sentence. Do not finish the
sentence and do not add an acknowledgment. Listen until they finish, then
respond to their latest request. Do not resume an answer they have replaced.
Patience: Hinglish speakers often pause mid-sentence with fillers such as
"haan…", "woh…", "matlab…", "ek minute", "umm" or a trailing "aur…", "ya
phir…". Treat these as the user still thinking: stay silent and keep
listening. Respond only once the request sounds complete.
Backchannel policy: Do not make listening sounds while the user is speaking.
"""

# Sent by the browser (session.instructions.append) when it detects barge-in or
# the assistant starting to talk while the user's turn is still incomplete.
YIELD_INSTRUCTION = (
    "The user is speaking or has not finished their sentence. Stop speaking now, "
    "stay silent and listen until they finish, then answer their latest request."
)


def live_instructions(base: str, mode: TurnTakingMode) -> str:
    if mode == "native":
        return base
    return base.rstrip("\n") + "\n" + TURN_TAKING_POLICY


def client_turn_taking(mode: TurnTakingMode) -> dict[str, str]:
    """What the browser needs to know about the session's turn-taking."""
    if mode == "smart_turn":
        return {"mode": mode, "yield_instruction": YIELD_INSTRUCTION}
    return {"mode": mode}
