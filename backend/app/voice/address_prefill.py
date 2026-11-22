"""Keep only address prefill the user actually said.

The model fills ``open_address_form`` from what it heard, and models copy
example values (form placeholders, prompt samples) into tool arguments. Every
field is therefore checked against the user's own recent words, read
server-side (the live transcript and the saved chat), never against anything
the model or the browser claims the user said.

Matching is deliberately loose about script and spelling: GPT-Live may
transcribe "Gachibowli" as "गचीबोवली", so both sides are reduced to a
consonant skeleton before comparing.
"""

import re
import unicodedata
from collections import deque
from collections.abc import Awaitable, Callable

# Sample values that have appeared in the form placeholders, prompts or tool
# descriptions. A field equal to one of these is dropped even when no user
# words are available.
KNOWN_SAMPLE_VALUES = frozenset(
    {
        "flat 4b",
        "4b",
        "lake view apartments",
        "indiranagar, bengaluru, karnataka",
        "indiranagar bengaluru karnataka",
        "near metro station",
        "street, area, city, state, pin code",
        "green park society",
        "green park",
        "flat 302",
        "302",
        "12 sample street, hsr layout, bengaluru",
        "12.971599",
        "77.594566",
    }
)

# Words that carry no address content of their own.
_STOPWORDS = frozenset(
    {
        "the", "a", "an", "of", "and", "near", "opp", "opposite", "behind", "next", "to",
        "flat", "house", "no", "number", "floor", "building", "society", "apartment",
        "apartments", "tower", "block", "road", "street", "main", "cross", "layout",
        "ke", "ka", "ki", "paas", "pass", "wala", "wali", "wale", "mein", "par",
    }
)

_PHONE = re.compile(r"(?:\+?\d[\s-]?){7,}")
_COORDINATE = re.compile(r"-?\d{1,3}\.\d{3,}")

_DEVANAGARI_CONSONANTS = {
    "क": "k", "ख": "k", "ग": "g", "घ": "g", "ङ": "n", "च": "c", "छ": "c", "ज": "j", "झ": "j",
    "ञ": "n", "ट": "t", "ठ": "t", "ड": "d", "ढ": "d", "ण": "n", "त": "t", "थ": "t", "द": "d",
    "ध": "d", "न": "n", "प": "p", "फ": "f", "ब": "b", "भ": "b", "म": "m", "य": "y", "र": "r",
    "ल": "l", "व": "v", "श": "s", "ष": "s", "स": "s", "ह": "h", "ळ": "l",
}
_NUKTA_FORMS = {"क": "k", "ख": "k", "ग": "g", "ज": "j", "ड": "r", "ढ": "r", "फ": "f"}
_DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
_NUMBER_WORDS = {
    "ek": "1", "one": "1", "pehla": "1", "pehli": "1", "first": "1",
    "do": "2", "two": "2", "dusra": "2", "doosra": "2", "dusri": "2", "second": "2",
    "teen": "3", "three": "3", "teesra": "3", "tisra": "3", "teesri": "3", "third": "3",
    "char": "4", "chaar": "4", "four": "4", "chautha": "4", "fourth": "4",
    "paanch": "5", "panch": "5", "five": "5", "fifth": "5",
    "chhah": "6", "chhe": "6", "six": "6", "saat": "7", "seven": "7",
    "aath": "8", "eight": "8", "nau": "9", "nine": "9", "das": "10", "ten": "10",
    "एक": "1", "दो": "2", "तीन": "3", "चार": "4", "पांच": "5", "पाँच": "5", "छह": "6",
    "सात": "7", "आठ": "8", "नौ": "9", "दस": "10", "पहला": "1", "पहली": "1", "दूसरा": "2",
    "दूसरी": "2", "तीसरा": "3", "तीसरी": "3",
}

MAX_REMEMBERED_UTTERANCES = 12
MAX_REMEMBERED_CHARS = 4_000


def _latin_skeleton(token: str) -> str:
    text = token.lower()
    for source, target in (("ph", "f"), ("th", "t"), ("dh", "d"), ("kh", "k"), ("gh", "g"),
                           ("bh", "b"), ("sh", "s"), ("ch", "c"), ("ck", "k"), ("q", "k"),
                           ("x", "ks"), ("z", "j"), ("w", "v"), ("c", "k")):
        text = text.replace(source, target)
    # "ch" became "c" above and then "k"; Devanagari च maps to "c" -> normalise both.
    return "".join(char for char in text if char.isalnum() and char not in "aeiouy")


def _devanagari_skeleton(token: str) -> str:
    out: list[str] = []
    chars = list(token)
    for index, char in enumerate(chars):
        if char == "\u093c":
            continue
        if index + 1 < len(chars) and chars[index + 1] == "\u093c" and char in _NUKTA_FORMS:
            out.append(_NUKTA_FORMS[char])
        elif char in _DEVANAGARI_CONSONANTS:
            out.append(_DEVANAGARI_CONSONANTS[char])
        elif char.isdigit():
            out.append(char)
    skeleton = "".join(out).replace("c", "k")
    return "".join(char for char in skeleton if char != "y")


def _tokens(text: str) -> list[str]:
    clean = unicodedata.normalize("NFC", text).translate(_DEVANAGARI_DIGITS)
    return re.findall(r"[\w\u0900-\u097F]+", clean.lower())


def _skeleton(token: str) -> str:
    if any("\u0900" <= char <= "\u097f" for char in token):
        return _devanagari_skeleton(token)
    return _latin_skeleton(token)


class _UserText:
    def __init__(self, text: str) -> None:
        tokens = _tokens(text)
        self.words = set(tokens)
        self.numbers = {token for token in tokens if token.isdigit()} | {
            _NUMBER_WORDS[token] for token in tokens if token in _NUMBER_WORDS
        }
        skeletons = [_skeleton(token) for token in tokens]
        self.skeletons = {value for value in skeletons if value}
        self.joined = " " + " ".join(value for value in skeletons if value) + " "
        self.concatenated = "".join(skeletons)

    def has(self, token: str) -> bool:
        if token in self.words:
            return True
        if token.isdigit():
            return token in self.numbers
        number = _NUMBER_WORDS.get(token)
        if number is not None and number in self.numbers:
            return True
        skeleton = _skeleton(token)
        if len(skeleton) < 2:
            return False
        if skeleton in self.skeletons:
            return True
        # Acronyms and run-together words ("RMZ" heard as "आरएमज़ेड").
        return len(skeleton) >= 3 and skeleton in self.concatenated


def _significant(tokens: list[str]) -> list[str]:
    return [token for token in tokens if token not in _STOPWORDS]


def is_sample_value(value: str) -> bool:
    return " ".join(value.lower().split()) in KNOWN_SAMPLE_VALUES


def looks_like_phone_or_coordinates(value: str) -> bool:
    return bool(_PHONE.search(value) or _COORDINATE.search(value))


def field_was_said(value: str, user_text: str) -> bool:
    """True when most content words of ``value`` appear in the user's words."""

    tokens = _tokens(value)
    significant = _significant(tokens) or tokens
    if not significant:
        return False
    heard = _UserText(user_text)
    matched = sum(1 for token in significant if heard.has(token))
    return matched * 10 >= len(significant) * 6


def filter_prefill(prefill: dict[str, str], user_text: str | None) -> tuple[dict[str, str], list[str]]:
    """(kept, dropped field names). ``type`` is an enum and kept as given.

    Without any user words only sample, phone-like and coordinate-like values
    are dropped: the form is still reviewed and edited by the user.
    """

    kept: dict[str, str] = {}
    dropped: list[str] = []
    for name, value in prefill.items():
        if name == "type":
            kept[name] = value
            continue
        if is_sample_value(value) or looks_like_phone_or_coordinates(value):
            dropped.append(name)
            continue
        if user_text and not field_was_said(value, user_text):
            dropped.append(name)
            continue
        kept[name] = value
    return kept, sorted(dropped)


UserTextLoader = Callable[[], Awaitable[str]]


class RecentUserWords:
    """The user's own recent words for one voice session or typed chat.

    Fed from server-side sources only: GPT-Live input transcripts seen by the
    sideband, typed messages received by the chat route, and (optionally) the
    saved chat's user turns. Kept in memory, never logged.
    """

    def __init__(self, loader: UserTextLoader | None = None) -> None:
        self.loader = loader
        self._utterances: deque[str] = deque(maxlen=MAX_REMEMBERED_UTTERANCES)
        self._partial = ""

    def add(self, text: str) -> None:
        clean = " ".join(str(text).split())
        if clean:
            self._utterances.append(clean[:MAX_REMEMBERED_CHARS])
        self._partial = ""

    def add_partial(self, delta: str) -> None:
        self._partial = (self._partial + str(delta))[-MAX_REMEMBERED_CHARS:]

    async def text(self) -> str:
        parts = [*self._utterances]
        if self._partial.strip():
            parts.append(self._partial.strip())
        if self.loader is not None:
            try:
                saved = await self.loader()
            except Exception:  # noqa: BLE001 - a missing chat must not block the form
                saved = ""
            if saved:
                parts.insert(0, saved)
        return " ".join(parts)[-MAX_REMEMBERED_CHARS * 2 :]
