"""Structured personal names.

The live failure this exists to prevent: ``full_name`` was split on the FIRST
space, so "Chandra Prakash Pandey" autofilled as first="CHANDRA",
last="PRAKASH PANDEY". The correct reading is first="Chandra",
middle="Prakash", last="Pandey" — but that is a *guess*, not a derivation:
"Maria Del Carmen Ruiz" and "Jean Luc Picard" split differently, and plenty of
cultures don't use a given/family split at all.

So this module never decides. It produces a SUGGESTION plus a ``certain`` flag.
Only ``certain`` suggestions may be applied without asking; everything else is
surfaced in the review UI for the user to confirm or correct, and only their
confirmation is persisted. Nothing here is ever used to overwrite a name the
user has already confirmed.
"""

from __future__ import annotations

from dataclasses import dataclass

# Lowercase surname particles ("van der Berg", "de la Cruz", "bin Rashid").
# When a token is a particle, it and everything after it belong to the family
# name — otherwise "Juan de la Cruz" would yield middle="de la", last="Cruz".
_SURNAME_PARTICLES: frozenset[str] = frozenset(
    {
        "van",
        "von",
        "de",
        "del",
        "della",
        "der",
        "den",
        "di",
        "da",
        "dos",
        "das",
        "du",
        "la",
        "le",
        "el",
        "al",
        "bin",
        "ibn",
        "bint",
        "ap",
        "mac",
        "mc",
        "st",
        "san",
        "santa",
        "ter",
        "ten",
        "op",
    }
)

# Honorifics and post-nominals are not name parts.
_PREFIXES: frozenset[str] = frozenset(
    {"mr", "mrs", "ms", "mx", "dr", "prof", "sir", "madam", "miss"}
)
_SUFFIXES: frozenset[str] = frozenset(
    {"jr", "sr", "ii", "iii", "iv", "v", "phd", "md", "mba", "esq", "dds", "rn"}
)


@dataclass(frozen=True)
class NameSuggestion:
    """A proposed split of a free-text name.

    ``certain`` is True only when the split is unambiguous (a single token, or
    exactly two tokens with no particles). Multi-token names are always False:
    they are proposed in the review UI, never applied silently.
    """

    first_name: str = ""
    middle_name: str = ""
    last_name: str = ""
    suffix: str = ""
    certain: bool = False

    @property
    def is_empty(self) -> bool:
        return not (self.first_name or self.middle_name or self.last_name)


def _strip_token(token: str) -> str:
    return token.strip().strip(",").strip()


def _key(token: str) -> str:
    return token.lower().strip(".,")


def normalize_display_case(name: str) -> str:
    """Title-case a name that arrived SHOUTING.

    Resume extraction routinely yields "CHANDRA PRAKASH PANDEY" because the
    header was styled in caps — that is a typography artifact, not how the
    person writes their name. Only fully-uppercase input is touched; any string
    that already contains a lowercase letter is left exactly as the user typed
    it (so "McDonald" and "van Rijn" survive untouched).
    """
    text = (name or "").strip()
    if not text or any(c.islower() for c in text):
        return text

    def fix(token: str) -> str:
        if _key(token) in _SURNAME_PARTICLES:
            return token.lower()
        # Hyphenated and apostrophed names capitalize each part ("O'Brien").
        out = token.capitalize()
        for sep in ("-", "'", "’"):
            if sep in out:
                out = sep.join(p.capitalize() for p in out.split(sep))
        return out

    return " ".join(fix(t) for t in text.split())


def suggest_name_parts(full_name: str) -> NameSuggestion:
    """Propose first/middle/last for a free-text name. Never authoritative.

    Handles the "Family, Given" convention, honorific prefixes, generational
    suffixes, lowercase surname particles, and all-caps input. Any name that
    still has three or more meaningful tokens is reported ``certain=False``.
    """
    raw = normalize_display_case(full_name)
    if not raw:
        return NameSuggestion()

    # "Pandey, Chandra Prakash" — comma form states the family name first, so
    # the split is explicit rather than guessed.
    if "," in raw:
        family_part, _, given_part = raw.partition(",")
        family = " ".join(t for t in family_part.split() if _key(t) not in _SUFFIXES).strip()
        given_tokens = [t for t in given_part.split() if _key(t) not in _PREFIXES]
        if family and given_tokens:
            return NameSuggestion(
                first_name=given_tokens[0],
                middle_name=" ".join(given_tokens[1:]),
                last_name=family,
                certain=True,
            )

    tokens = [_strip_token(t) for t in raw.split()]
    tokens = [t for t in tokens if t]

    # Drop leading honorifics.
    while tokens and _key(tokens[0]) in _PREFIXES:
        tokens.pop(0)

    # Peel trailing post-nominals ("Chandra Pandey Jr.").
    suffix_parts: list[str] = []
    while len(tokens) > 1 and _key(tokens[-1]) in _SUFFIXES:
        suffix_parts.insert(0, tokens.pop())
    suffix = " ".join(suffix_parts)

    if not tokens:
        return NameSuggestion(suffix=suffix)

    if len(tokens) == 1:
        # A mononym: it is the given name, and there is nothing to guess.
        return NameSuggestion(first_name=tokens[0], suffix=suffix, certain=True)

    # A surname particle anchors the family name: everything from the first
    # particle onward is the last name.
    particle_at = next(
        (i for i, t in enumerate(tokens) if i > 0 and _key(t) in _SURNAME_PARTICLES),
        None,
    )
    if particle_at is not None:
        return NameSuggestion(
            first_name=tokens[0],
            middle_name=" ".join(tokens[1:particle_at]),
            last_name=" ".join(tokens[particle_at:]),
            suffix=suffix,
            certain=False,
        )

    if len(tokens) == 2:
        return NameSuggestion(
            first_name=tokens[0], last_name=tokens[1], suffix=suffix, certain=True
        )

    # Three or more tokens: the common Western reading is first / middle… / last,
    # which is what we PROPOSE — but it is exactly the case that produced the
    # "PRAKASH PANDEY" bug, so it is never certain.
    return NameSuggestion(
        first_name=tokens[0],
        middle_name=" ".join(tokens[1:-1]),
        last_name=tokens[-1],
        suffix=suffix,
        certain=False,
    )


def looks_machine_split(full_name: str, first_name: str, last_name: str) -> bool:
    """True when a stored first/last pair is exactly what the OLD naive
    "split on the first space" rule would have produced.

    That rule is how ``name_confirmed`` came to be True for splits the user
    never actually saw — "CHANDRA PRAKASH PANDEY" was stored as
    first="CHANDRA", last="PRAKASH PANDEY" and marked confirmed. Such a
    confirmation carries no user intent, so it is withdrawn (not corrected)
    and re-asked.

    Only multi-token surnames can match, so an ordinary two-token name that the
    user really did confirm is never disturbed.
    """
    tokens = (full_name or "").split()
    first = (first_name or "").strip()
    last = (last_name or "").strip()
    if len(tokens) < 3 or not first or not last:
        return False
    # A single-token surname could not have come from the naive rule on a
    # 3+ token name, so only the "rest of the string" shape is suspect.
    return first == tokens[0] and last == " ".join(tokens[1:])


def compose_full_name(
    first_name: str | None,
    middle_name: str | None = None,
    last_name: str | None = None,
    suffix: str | None = None,
) -> str:
    """Join structured parts back into a display full name.

    ``full_name`` is a computed/display value only — the structured parts are
    the source of truth.
    """
    parts = [
        (first_name or "").strip(),
        (middle_name or "").strip(),
        (last_name or "").strip(),
        (suffix or "").strip(),
    ]
    return " ".join(p for p in parts if p)


def resolve_preferred_names(
    *,
    preferred_first_name: str | None,
    preferred_last_name: str | None,
    first_name: str | None,
    last_name: str | None,
    legal_fallback_allowed: bool = True,
) -> tuple[str, str]:
    """Resolve preferred first/last, falling back to the legal name.

    The fallback is only correct when the form itself says to use the legal name
    when no preferred name exists (Greenhouse's "Preferred Name" helper text
    typically does). Callers that cannot establish that pass
    ``legal_fallback_allowed=False`` and get empty strings, so the field is
    surfaced as a question instead of being filled with an assumption.
    """
    first = (preferred_first_name or "").strip()
    last = (preferred_last_name or "").strip()
    if not legal_fallback_allowed:
        return first, last
    return first or (first_name or "").strip(), last or (last_name or "").strip()
