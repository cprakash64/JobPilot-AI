"""Structured phone numbers.

The live failure this exists to prevent: the profile stored the bare string
"602-816-1309" and the extension typed it verbatim into a Greenhouse phone
field that had already been set to "United States (+1)". The site rejected it,
and nothing detected the rejection.

A phone number is therefore stored as four derived-together fields:

    country_code    "+1"
    country_iso2    "US"
    national_number "6028161309"
    e164            "+16028161309"

so the extension can answer "which country do I select?" and "what do I type?"
separately, and never has to concatenate strings to reconstruct one from the
other. Parsing uses ``phonenumbers`` (Google's libphonenumber port) rather than
regex heuristics.
"""

from __future__ import annotations

from dataclasses import dataclass

import phonenumbers

DEFAULT_REGION = "US"


@dataclass(frozen=True)
class PhoneParts:
    """A parsed phone number. ``valid`` is False for numbers that parsed
    structurally but are not dialable, so callers can keep the user's raw text
    and flag it rather than autofilling something a site will reject."""

    country_code: str = ""
    country_iso2: str = ""
    national_number: str = ""
    e164: str = ""
    valid: bool = False

    @property
    def is_empty(self) -> bool:
        return not self.e164 and not self.national_number

    def national_formatted(self) -> str:
        """The number as a person in that country would write it
        ("(602) 816-1309"). Some sites' input masks accept only this."""
        if not self.e164:
            return ""
        try:
            parsed = phonenumbers.parse(self.e164, None)
        except phonenumbers.NumberParseException:
            return self.national_number
        return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.NATIONAL)


def parse_phone(raw: str | None, default_region: str = DEFAULT_REGION) -> PhoneParts:
    """Parse free-text phone input into structured parts.

    ``default_region`` is only consulted when the input carries no "+" country
    prefix — an explicit prefix always wins, so a stored international number is
    never re-homed to the default region.
    """
    text = (raw or "").strip()
    if not text:
        return PhoneParts()

    region = (default_region or DEFAULT_REGION).upper() or DEFAULT_REGION
    try:
        parsed = phonenumbers.parse(text, None if text.startswith("+") else region)
    except phonenumbers.NumberParseException:
        return PhoneParts()

    country_code = f"+{parsed.country_code}"
    national = str(parsed.national_number)
    iso2 = phonenumbers.region_code_for_number(parsed) or ""
    valid = phonenumbers.is_valid_number(parsed)

    return PhoneParts(
        country_code=country_code,
        country_iso2=iso2,
        national_number=national,
        e164=phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164),
        valid=valid,
    )


def parts_from_stored(
    *,
    country_code: str | None,
    country_iso2: str | None,
    national_number: str | None,
    e164: str | None,
    legacy_phone: str | None = None,
    default_region: str = DEFAULT_REGION,
) -> PhoneParts:
    """Build ``PhoneParts`` from persisted columns, re-deriving from the legacy
    free-text ``phone`` column when the structured columns are still empty.

    This is the backward-compatibility path for profiles saved before the
    structured columns existed; it re-parses rather than string-splitting.
    """
    if e164:
        return parse_phone(e164, default_region)
    if country_code and national_number:
        return parse_phone(f"{country_code}{national_number}", default_region)
    if national_number and country_iso2:
        return parse_phone(national_number, country_iso2)
    return parse_phone(legacy_phone, default_region)
