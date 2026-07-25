"""Map a candidate's CONFIRMED answer onto an employer dropdown's exact option
wording (section E, priority 5 — used only after exact and controlled-alias
matching have failed, and always before asking the user).

Two invariants are enforced here in code, never left to the model:

1. The model may only ever RETURN one of the option labels we supplied. Its
   output is validated against that exact set; anything else is discarded.
2. The model may never DECIDE a consequential answer. For sponsorship, work
   authorization, consent/policy acknowledgement, criminal history and every
   demographic/EEO category, a mapping is attempted only when the user has
   already supplied and confirmed the underlying answer — the model is then
   restricted to translating that answer into the employer's phrasing.

Anything the model cannot map becomes "ask the user", never a guess.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.ai.provider import ai_provider
from app.applications.canonical import SENSITIVE_KEYS

# Categories whose answers carry legal or consent consequences. The model may only
# translate an already-confirmed answer for these; it may never originate one.
CONFIRMATION_REQUIRED_KEYS: frozenset[str] = SENSITIVE_KEYS | frozenset(
    {
        "work_authorization_us",
        "sponsorship_required_now",
        "sponsorship_required_future",
        "consent_processing",
        "policy_acknowledgement",
    }
)

# Below this, the extension must ask the user rather than fill.
AUTO_APPLY_CONFIDENCE = 0.9


@dataclass(frozen=True)
class OptionMapping:
    selected_option_label: str | None
    confidence: float
    requires_user_confirmation: bool
    reason: str

    @property
    def usable(self) -> bool:
        """Safe to fill automatically without asking the user first."""
        return (
            self.selected_option_label is not None
            and not self.requires_user_confirmation
            and self.confidence >= AUTO_APPLY_CONFIDENCE
        )


def _ask_user(reason: str) -> OptionMapping:
    return OptionMapping(
        selected_option_label=None,
        confidence=0.0,
        requires_user_confirmation=True,
        reason=reason,
    )


async def map_option(
    *,
    question_label: str,
    options: list[str],
    canonical_key: str,
    confirmed_answer: str | None,
    help_text: str = "",
) -> OptionMapping:
    """Return the employer option matching ``confirmed_answer``, or an
    ask-the-user result. Never raises for a provider failure."""
    clean_options = [o for o in (opt.strip() for opt in options) if o]
    if not clean_options:
        return _ask_user("The control offered no options to choose from.")

    answer = (confirmed_answer or "").strip()

    # Rule 2 — a consequential question with no confirmed answer is NEVER sent to
    # the model, so it cannot originate the response even accidentally.
    if not answer:
        if canonical_key in CONFIRMATION_REQUIRED_KEYS:
            return _ask_user("This question needs your own confirmed answer.")
        return _ask_user("JobPilot has no confirmed answer to map.")

    result = await ai_provider.json_task(
        "map_dropdown_option.md",
        {
            "question_label": question_label,
            "help_text": help_text,
            "canonical_key": canonical_key,
            "confirmed_answer": answer,
            "options": clean_options,
        },
        smart=False,
    )
    if not result.ai_used:
        return _ask_user("AI mapping was unavailable; please choose an option.")

    return _validate(result.data, clean_options)


def _validate(data: dict, options: list[str]) -> OptionMapping:
    """Rule 1 — the label MUST be one we supplied, character for character."""
    raw_label = data.get("selected_option_label")
    label = raw_label.strip() if isinstance(raw_label, str) else None
    if label is not None and label not in options:
        # The model invented or reformatted a label: discard the whole mapping.
        return _ask_user("The suggested option did not exactly match this employer's choices.")

    try:
        confidence = float(data.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    requires_confirmation = bool(data.get("requires_user_confirmation", True))
    if label is None:
        requires_confirmation = True
    if confidence < AUTO_APPLY_CONFIDENCE:
        requires_confirmation = True

    reason = str(data.get("reason") or "").strip()[:200]
    return OptionMapping(
        selected_option_label=label,
        confidence=confidence,
        requires_user_confirmation=requires_confirmation,
        reason=reason or "No explanation provided.",
    )
