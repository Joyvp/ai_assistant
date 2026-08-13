"""Automatic fact capture — notice things worth remembering, without being told.

Typing ``/remember`` every time you mention something is friction, and friction
means the memory stays empty. This module reads each thing you say and pulls
out the small number of statements that are clearly durable facts about you:
where you live, what you are called, what you are building, what you use, what
you like.

Three rules govern the design:

*   **Rule-based, not model-based.** No extra generation call, no waiting, no
    second model hallucinating facts about you. Regexes over a fixed list of
    high-signal phrasings. If a pattern is not in the list, nothing is saved.

*   **Never silent.** Master spec §15 forbids silent memory. Auto-capture is
    always announced on screen the moment it happens, with the id needed to
    undo it, and it can be switched off entirely.

*   **Precision over recall.** Missing a fact costs one ``/remember``. Saving a
    wrong one pollutes every future prompt. Every rule refuses questions,
    hypotheticals and negations, and stops at the end of the clause.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


# Phrases that mean the sentence is not a statement of fact about the user.
# "where do i live", "if i lived in tokyo", "should i use rust".
_NOT_A_STATEMENT = re.compile(
    r"^\s*(where|what|who|when|why|how|which|do|does|did|am|are|is|was|were|"
    r"can|could|should|would|will|shall|if|whether|suppose|imagine|say)\b",
    re.IGNORECASE,
)

# Fragments that flip or soften the meaning enough that we should stay out.
_HEDGES = (
    "used to",
    "no longer",
    "don't think",
    "dont think",
    "not sure",
    "maybe",
    "might be",
    "probably",
    "i wish",
    "pretend",
)

# Where a captured clause ends. A full stop only counts when whitespace or the
# end of the string follows it, so "joy@example.com" and "phi3:mini" and
# "version 1.2" survive intact.
_CLAUSE_END = re.compile(
    r"\s+(?:and|but|so|then|because|although|though|while|which|that's|thats)\s+|"
    r"[,;]|[.!?](?:\s|$)|\s+-\s+"
)

# Words that should keep their capital in a name or a place, because "i live in
# saskatoon" reads badly in a prompt as "saskatoon".
_LOWERCASE_PARTICLES = {"of", "the", "de", "la", "von", "van", "upon", "on", "and"}

# Trailing filler that adds nothing to a stored fact.
_TRAILING_FLUFF = re.compile(
    r"\s+(?:right now|at the moment|these days|currently|nowadays|i guess|"
    r"i think|btw|by the way|lol|haha|"
    r"(?:last|this|next)\s+(?:year|month|week|summer|winter)|"
    r"a (?:few|couple of) (?:years|months|weeks) ago|"
    r"in \d{4})\s*$",
    re.IGNORECASE,
)

_MIN_SUBJECT = 2
_MAX_SUBJECT = 80


@dataclass(frozen=True)
class Candidate:
    """A fact worth storing, in the form it should be stored."""

    text: str
    kind: str
    key: str | None = None
    """Identity slot this fact occupies.

    Facts with a key are singular — you have one name and one home — so a new
    one replaces the old. Facts without a key accumulate.
    """


@dataclass(frozen=True)
class _Rule:
    kind: str
    pattern: re.Pattern[str]
    template: str
    key: str | None = None


def _rule(
    kind: str,
    regex: str,
    template: str,
    key: str | None = None,
    *,
    cased: bool = False,
) -> _Rule:
    """Compile one rule. ``cased=True`` keeps the pattern case-sensitive."""
    flags = 0 if cased else re.IGNORECASE
    return _Rule(kind, re.compile(regex, flags), template, key)


# Ordered: the first rule that matches a sentence wins, so the more specific
# phrasings come first.
RULES: tuple[_Rule, ...] = (
    # -- identity ----------------------------------------------------------
    _rule("name", r"\bmy name(?:'s| is)\s+(.+)", "My name is {}", key="name"),
    _rule("name", r"\b(?:you can )?call me\s+(.+)", "My name is {}", key="name"),
    _rule("name", r"\bi'?m called\s+(.+)", "My name is {}", key="name"),
    # Bare "I'm Joy" is only safe when they capitalised the word — otherwise
    # "im tired" becomes a name. Case-sensitive on purpose, and anchored to
    # the whole clause so "I'm Joy from Regina" does not slip through.
    _rule(
        "name",
        r"^[Ii]'?m ([A-Z][a-z]+)\s*$",
        "My name is {}",
        key="name",
        cased=True,
    ),
    _rule(
        "pronouns",
        r"\bmy pronouns are\s+(.+)",
        "My pronouns are {}",
        key="pronouns",
    ),
    _rule(
        "birthday",
        r"\bmy birthday(?:'s| is)\s+(.+)",
        "My birthday is {}",
        key="birthday",
    ),
    # -- place -------------------------------------------------------------
    _rule("location", r"\bi live in\s+(.+)", "I live in {}", key="location"),
    _rule(
        "location",
        r"\bi'?m (?:based|living) in\s+(.+)",
        "I live in {}",
        key="location",
    ),
    _rule("location", r"\bi moved to\s+(.+)", "I live in {}", key="location"),
    _rule("origin", r"\bi'?m from\s+(.+)", "I am from {}", key="origin"),
    # -- work --------------------------------------------------------------
    _rule("role", r"\bi work as (?:an?\s+)?(.+)", "I work as a {}", key="role"),
    _rule("role", r"\bi study\s+(.+)", "I study {}", key="role"),
    _rule("role", r"\bi'?m (?:an?\s+)?(student|developer|programmer|engineer|"
                  r"designer|teacher|nurse|writer|artist)\b",
          "I am a {}", key="role"),
    # -- projects ----------------------------------------------------------
    _rule(
        "project",
        r"\bmy project(?:'s| is) (?:called\s+)?(.+)",
        "My project is called {}",
    ),
    _rule("project", r"\bi'?m (?:building|making|writing)\s+(.+)", "I am building {}"),
    _rule("project", r"\bi'?m working on\s+(.+)", "I am working on {}"),
    # -- setup -------------------------------------------------------------
    _rule("setup", r"\bi use\s+(.+)", "I use {}"),
    _rule("setup", r"\bmy (?:laptop|computer|machine|pc) is (?:an?\s+)?(.+)",
          "My computer is a {}", key="computer"),
    _rule("setup", r"\bi run\s+((?:linux|mint|ubuntu|debian|arch|fedora|macos|"
                   r"windows|xfce|gnome|kde)\b.*)", "I run {}", key="os"),
    # -- taste -------------------------------------------------------------
    _rule("dislike", r"\bi (?:don'?t|do not) like\s+(.+)", "I do not like {}"),
    _rule("dislike", r"\bi hate\s+(.+)", "I do not like {}"),
    _rule("preference", r"\bi prefer\s+(.+)", "I prefer {}"),
    _rule("preference", r"\bi really like\s+(.+)", "I like {}"),
    _rule("preference", r"\bmy favou?rite\s+(.+?)\s+is\s+(.+)", "My favourite {} is {}"),
    # -- allergies / hard constraints, worth never forgetting --------------
    _rule("health", r"\bi'?m allergic to\s+(.+)", "I am allergic to {}"),
)

# "remember (that) X" is an explicit instruction in plain English rather than a
# slash command. Handled separately because the whole clause is the fact.
_EXPLICIT = re.compile(
    r"^(?:please\s+)?(?:remember|note|keep in mind)\s+(?:that\s+)?(.+)$",
    re.IGNORECASE,
)


def _normalise(text: str) -> str:
    """Smooth over the ways people write the same thing.

    Curly apostrophes become straight ones, and "i am" becomes "i'm", so each
    rule only has to spell one variant.
    """
    text = text.replace("\u2019", "'").replace("\u02bc", "'")
    text = re.sub(r"\bi am\b", "i'm", text, flags=re.IGNORECASE)
    text = re.sub(r"\bi do not\b", "i don't", text, flags=re.IGNORECASE)
    return text


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    return [p.strip() for p in parts if p.strip()]


def _clauses(sentence: str) -> list[str]:
    """Split a sentence at joins, so one line can yield more than one fact.

    "im joy and i live in saskatoon" is two facts, not one.
    """
    parts = re.split(r"\s+(?:and|but|so|then)\s+|\s*[,;]\s*", sentence)
    return [p.strip() for p in parts if p.strip()]


def _tidy(subject: str) -> str:
    """Trim a captured subject down to the fact itself."""
    cut = _CLAUSE_END.split(subject, maxsplit=1)[0]
    cut = _TRAILING_FLUFF.sub("", cut)
    return cut.strip(" \t'\"“”‘’-").strip()


def _plausible(subject: str) -> bool:
    if not (_MIN_SUBJECT <= len(subject) <= _MAX_SUBJECT):
        return False
    # A subject that is only punctuation or digits is noise.
    return bool(re.search(r"[a-z]", subject, re.IGNORECASE))


def _capitalise(text: str) -> str:
    return text[0].upper() + text[1:] if text else text


def _titlecase(text: str) -> str:
    """Capitalise a name or place the user typed in lower case.

    Left alone if they already used capitals — they know how their own name is
    spelled better than a regex does.
    """
    if any(c.isupper() for c in text):
        return text

    words = text.split()
    return " ".join(
        _capitalise(w) if w not in _LOWERCASE_PARTICLES and w != "the" else w
        for w in words
    )


# Fact kinds whose subject is a proper noun.
_PROPER = {"name", "location", "origin"}


def extract(message: str) -> list[Candidate]:
    """Pull durable facts out of one user message.

    Returns an empty list far more often than not — that is the point.
    """
    if not message or message.lstrip().startswith("/"):
        return []

    found: list[Candidate] = []
    seen: set[str] = set()

    for sentence in _sentences(_normalise(message)):
        if "?" in sentence:
            continue

        explicit = _EXPLICIT.match(sentence)
        if explicit:
            subject = _tidy(explicit.group(1))
            if _plausible(subject):
                text = _capitalise(subject)
                if text.lower() not in seen:
                    seen.add(text.lower())
                    found.append(Candidate(text, "explicit"))
            continue

        for clause in _clauses(sentence):
            _scan(clause, found, seen)

    return found


def _scan(clause: str, found: list[Candidate], seen: set[str]) -> None:
    """Match one clause against the rules, appending at most one fact."""
    lowered = clause.lower()

    if _NOT_A_STATEMENT.match(clause):
        return
    if any(h in lowered for h in _HEDGES):
        return

    for rule in RULES:
        match = rule.pattern.search(clause)
        if not match:
            continue

        groups = [_tidy(g) for g in match.groups()]
        if not all(_plausible(g) for g in groups):
            return

        if rule.kind in _PROPER:
            groups = [_titlecase(g) for g in groups]

        text = rule.template.format(*groups)
        if text.lower() not in seen:
            seen.add(text.lower())
            found.append(Candidate(text, rule.kind, rule.key))
        return  # one fact per clause
