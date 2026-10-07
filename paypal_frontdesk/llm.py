"""Intent + slot extraction for customer messages.

Two extractors, one protocol — ``extractor(text) -> dict`` with any of the
keys ``intent``, ``name``, ``service``, ``date``, ``time``, ``phone``,
``email``:

- :class:`RulesExtractor` — deterministic regex/slot-filling. Always
  available, fully offline. This is the honest default when no LLM API key
  is configured: nothing is faked, the parsing is rules-based.
- :class:`LLMExtractor` — an OpenAI-compatible chat-completions adapter
  configured purely from environment variables (``OPENAI_BASE_URL``,
  ``OPENAI_API_KEY``, ``OPENAI_MODEL``). Pure stdlib ``urllib``.

:func:`make_extractor` wires them together: when a key is present the LLM
result fills gaps the rules missed (rules win ties, so a hallucinated field
can never silently overwrite a solid regex parse); without a key the agent
runs rules-only and says so.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from datetime import date, timedelta

from .config import Service

# ---------------------------------------------------------------------------
# Rules-based extraction (deterministic, offline)
# ---------------------------------------------------------------------------

_WORD_NUM = {
    "zero": 0, "oh": 0, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
}

_WORD_HOUR = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
}

_WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}

INTENTS = ("book", "cancel", "invoice", "confirm", "deny", "paid", "unknown")

_CANCEL_RE = re.compile(r"\b(cancel|call off|reschedule|can't make it|cannot make it)\b", re.I)
_INVOICE_RE = re.compile(r"\b(invoice|bill|balance|charge me|receipt|pay the rest)\b", re.I)
_PAID_RE = re.compile(
    r"\b(i (just )?(paid|approved|did it)|payment (went through|is done|complete)|"
    r"done paying|all paid|paid already)\b",
    re.I,
)
_YES_RE = re.compile(r"\b(yes|yeah|yep|correct|right|sure|confirm|sounds good|that's right|y)\b", re.I)
_NO_RE = re.compile(r"\b(no|nope|wrong|incorrect|change|fix|actually|n)\b", re.I)


def extract_intent(text: str) -> str:
    """Classify a message into a coarse intent using keyword rules."""
    if _PAID_RE.search(text):
        return "paid"
    if _CANCEL_RE.search(text):
        return "cancel"
    if _INVOICE_RE.search(text):
        return "invoice"
    if _YES_RE.search(text) and not _NO_RE.search(text):
        return "confirm"
    if _NO_RE.search(text):
        return "deny"
    return "book"


def _spoken_digit_runs(text: str) -> list[str]:
    """Collapse consecutive spoken digit words/numbers into digit strings."""
    tokens = re.findall(r"[a-z]+|\d+", text.lower())
    runs: list[str] = []
    current: list[str] = []
    for tok in tokens:
        if tok.isdigit():
            current.append(tok)
        elif tok in _WORD_NUM:
            current.append(str(_WORD_NUM[tok]))
        elif current:
            runs.append("".join(current))
            current = []
    if current:
        runs.append("".join(current))
    return runs


def normalize_phone(text: str) -> str | None:
    """Extract a US phone number from text, formatted '(XXX) XXX-XXXX'."""
    candidates = [r for r in _spoken_digit_runs(text) if 10 <= len(r) <= 11]
    if not candidates:
        return None
    number = max(candidates, key=len)
    if len(number) == 11 and number.startswith("1"):
        number = number[1:]
    if len(number) != 10:
        return None
    return f"({number[0:3]}) {number[3:6]}-{number[6:10]}"


_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")


def extract_email(text: str) -> str | None:
    m = _EMAIL_RE.search(text)
    return m.group(0).lower() if m else None


def parse_date(text: str, today: date) -> str | None:
    """Parse a natural-language date into ISO format relative to ``today``."""
    t = text.lower()

    if "day after tomorrow" in t:
        return (today + timedelta(days=2)).isoformat()
    if re.search(r"\btomorrow\b", t):
        return (today + timedelta(days=1)).isoformat()
    if re.search(r"\btoday\b", t):
        return today.isoformat()

    m = re.search(r"\b(?:(next|this)\s+)?(" + "|".join(_WEEKDAYS) + r")\b", t)
    if m:
        qualifier = m.group(1)
        weekday = _WEEKDAYS[m.group(2)]
        delta = (weekday - today.weekday()) % 7
        if qualifier == "next":
            delta += 7
        elif qualifier == "this":
            pass
        elif delta == 0:
            delta = 7  # bare weekday on that weekday means a week out
        return (today + timedelta(days=delta)).isoformat()

    month_alt = "|".join(_MONTHS)
    m = re.search(rf"\b({month_alt})\s+(\d{{1,2}})(?:st|nd|rd|th)?\b", t)
    if m:
        month = _MONTHS[m.group(1)]
        day = int(m.group(2))
        try:
            target = date(today.year, month, day)
        except ValueError:
            return None
        if target < today:
            target = date(today.year + 1, month, day)
        return target.isoformat()

    m = re.search(r"\b(\d{1,2})[/\-](\d{1,2})\b", t)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        try:
            target = date(today.year, month, day)
        except ValueError:
            return None
        if target < today:
            target = date(today.year + 1, month, day)
        return target.isoformat()

    # ISO dates pass straight through.
    m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", t)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
        except ValueError:
            return None

    return None


def parse_time(text: str) -> str | None:
    """Parse a natural-language time into 24h 'HH:MM'."""
    t = text.lower()

    if re.search(r"\bnoon\b", t):
        return "12:00"
    if re.search(r"\bmidnight\b", t):
        return "00:00"

    m = re.search(r"\b(\d{1,2}):(\d{2})\s*(a\.?m\.?|p\.?m\.?)?\b", t)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2))
        meridiem = (m.group(3) or "").replace(".", "")
        if meridiem == "pm" and hour < 12:
            hour += 12
        elif meridiem == "am" and hour == 12:
            hour = 0
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return f"{hour:02d}:{minute:02d}"
        return None

    hour_words = "|".join(_WORD_HOUR)
    m = re.search(
        rf"\b(\d{{1,2}}|{hour_words})"
        r"(?:\s+(thirty|fifteen|forty[- ]five|oh\s+\w+|\d{2}))?"
        r"(?:\s*(a\.?m\.?|p\.?m\.?|o'clock)|\s+in\s+the\s+(morning|afternoon|evening))\b",
        t,
    )
    if not m:
        return None

    raw_hour = m.group(1)
    hour = int(raw_hour) if raw_hour.isdigit() else _WORD_HOUR[raw_hour]
    minute = 0
    if m.group(2):
        minute = _word_minute(m.group(2).replace("-", " "))
        if minute is None:
            return None
    meridiem = (m.group(3) or "").replace(".", "")
    daypart = m.group(4)

    if meridiem == "pm" or daypart in ("afternoon", "evening"):
        if hour < 12:
            hour += 12
    elif meridiem == "am" and hour == 12:
        hour = 0

    if 0 <= hour <= 23 and 0 <= minute <= 59:
        return f"{hour:02d}:{minute:02d}"
    return None


def _word_minute(text: str) -> int | None:
    text = text.strip()
    if text.isdigit():
        return int(text)
    named = {"thirty": 30, "fifteen": 15, "forty five": 45}
    if text in named:
        return named[text]
    if text.startswith("oh "):
        tail = text[3:].strip()
        if tail.isdigit():
            return int(tail)
        if tail in _WORD_NUM:
            return _WORD_NUM[tail]
    return None


def match_service(text: str, services: tuple[Service, ...]) -> Service | None:
    """Match a message against the service catalog (name or alias)."""
    t = text.lower()
    best: Service | None = None
    best_len = 0
    for svc in services:
        for needle in (svc.name.lower(), *svc.aliases):
            if needle and needle in t and len(needle) > best_len:
                best, best_len = svc, len(needle)
    return best


_NAME_RE = re.compile(
    r"\b(?:my name is|this is|i am|i'm|im|it's|its)\s+([a-z]+(?:\s+[a-z]+)?)\b",
    re.IGNORECASE,
)
_NAME_STOPWORDS = {
    "calling", "here", "looking", "interested", "wondering", "hoping",
    "the", "a", "an", "not", "just", "about", "for", "paid", "done",
}


def extract_name(text: str) -> str | None:
    m = _NAME_RE.search(text)
    if not m:
        return None
    words = [w for w in m.group(1).split() if w.lower() not in _NAME_STOPWORDS]
    if not words:
        return None
    return " ".join(w.capitalize() for w in words[:3])


class RulesExtractor:
    """Deterministic slot + intent extraction. Always available offline."""

    def __init__(self, services: tuple[Service, ...], today: date | None = None) -> None:
        self.services = services
        self.today = today or date.today()

    def __call__(self, text: str) -> dict[str, str]:
        found: dict[str, str] = {"intent": extract_intent(text)}
        name = extract_name(text)
        if name:
            found["name"] = name
        phone = normalize_phone(text)
        if phone:
            found["phone"] = phone
        email = extract_email(text)
        if email:
            found["email"] = email
        svc = match_service(text, self.services)
        if svc:
            found["service"] = svc.name
        day = parse_date(text, self.today)
        if day:
            found["date"] = day
        tm = parse_time(text)
        if tm:
            found["time"] = tm
        return found


# ---------------------------------------------------------------------------
# LLM extraction (OpenAI-compatible chat completions, env-configured)
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You extract booking details from customer messages for {business}.
Services offered: {services}.
Today is {today}.

Reply with ONLY a JSON object (no prose, no markdown fences) with any of
these keys you can find in the message:
  "intent": one of {intents} — "book" for a new booking, "cancel" to cancel
    an existing booking, "invoice" to be billed, "paid" when the customer
    says they completed payment, "confirm"/"deny" for yes/no answers.
  "name": customer name
  "service": the exact service name from the list above
  "date": ISO date YYYY-MM-DD (resolve words like "next Friday" against today)
  "time": 24h HH:MM
  "phone": customer phone
  "email": customer email
Omit keys you cannot fill. Do not invent values."""


class LLMExtractor:
    """OpenAI-compatible chat-completions extractor (stdlib urllib only).

    Any transport or parsing failure yields an empty dict so callers can
    fall back to rules — the agent never invents slots.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        services: tuple[Service, ...],
        business_name: str = "the business",
        today: date | None = None,
        timeout: float = 20.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.services = services
        self.business_name = business_name
        self.today = today or date.today()
        self.timeout = timeout

    def __call__(self, text: str) -> dict[str, str]:
        prompt = _SYSTEM_PROMPT.format(
            business=self.business_name,
            services=", ".join(s.name for s in self.services),
            today=self.today.isoformat(),
            intents=", ".join(f'"{i}"' for i in INTENTS),
        )
        body = json.dumps(
            {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": text},
                ],
                "temperature": 0,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/v1/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            content = payload["choices"][0]["message"]["content"]
            parsed = _parse_json_object(content)
        except Exception:
            return {}
        return _sanitize_slots(parsed)


def _parse_json_object(content: str) -> dict:
    """Pull the first JSON object out of an LLM reply."""
    start = content.find("{")
    end = content.rfind("}")
    if start == -1 or end <= start:
        return {}
    data = json.loads(content[start : end + 1])
    return data if isinstance(data, dict) else {}


def _sanitize_slots(parsed: dict) -> dict[str, str]:
    allowed = {"intent", "name", "service", "date", "time", "phone", "email"}
    out: dict[str, str] = {}
    for key, value in parsed.items():
        if key in allowed and isinstance(value, str) and value.strip():
            out[key] = value.strip()
    if out.get("intent") not in INTENTS:
        out.pop("intent", None)
    return out


class HybridExtractor:
    """Rules first, LLM fills gaps. Deterministic parsing wins ties."""

    def __init__(self, rules: RulesExtractor, llm: LLMExtractor) -> None:
        self.rules = rules
        self.llm = llm

    def __call__(self, text: str) -> dict[str, str]:
        found = self.rules(text)
        try:
            llm_found = self.llm(text) or {}
        except Exception:
            llm_found = {}
        for key, value in llm_found.items():
            found.setdefault(key, value)
        return found


def make_extractor(
    services: tuple[Service, ...],
    business_name: str = "the business",
    today: date | None = None,
    environ: dict[str, str] | None = None,
) -> tuple[RulesExtractor | HybridExtractor, str]:
    """Build the best available extractor from environment configuration.

    Returns ``(extractor, description)`` where the description honestly
    states which extraction path is live.
    """
    env = os.environ if environ is None else environ
    rules = RulesExtractor(services, today)
    api_key = env.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        return rules, "rules-based extraction (no OPENAI_API_KEY configured)"
    llm = LLMExtractor(
        base_url=env.get("OPENAI_BASE_URL", "https://api.openai.com"),
        api_key=api_key,
        model=env.get("OPENAI_MODEL", "gpt-4o-mini"),
        services=services,
        business_name=business_name,
        today=today,
    )
    return (
        HybridExtractor(rules, llm),
        f"LLM extraction ({env.get('OPENAI_MODEL', 'gpt-4o-mini')} via "
        f"{env.get('OPENAI_BASE_URL', 'https://api.openai.com')}) with "
        "rules-based fallback",
    )
