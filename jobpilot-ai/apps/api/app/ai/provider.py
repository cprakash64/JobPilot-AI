import json
import logging
from pathlib import Path
from typing import Any

import openai
from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict

from app.core.config import settings

logger = logging.getLogger("jobpilot.ai")
PROMPT_DIR = Path(__file__).parent / "prompts"

# Machine reason -> human sentence fragment used in "Generated with template mode
# because <reason>." messages. Never contains the API key.
_REASON_TEXT = {
    "no_key": "no OpenAI API key is configured",
    "model_not_found": "the configured model was not found",
    "auth": "the OpenAI API key was rejected",
    "rate_limit": "the AI provider rate-limited the request",
    "request": "the AI request failed",
}


class AIResult(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    data: dict[str, Any]
    model_used: str
    ai_used: bool = False
    # Sanitized failure reason (never contains secrets); None when AI succeeded.
    error: str | None = None
    error_reason: str | None = None  # machine code: no_key|model_not_found|auth|rate_limit|request


class AIProvider:
    def __init__(self) -> None:
        self._api_key = settings.openai_api_key
        self.client = AsyncOpenAI(api_key=self._api_key) if self._api_key else None
        self.last_error: str | None = None

    # --- diagnostics (never expose the key itself) ------------------------- #
    @property
    def api_key_present(self) -> bool:
        return bool(self._api_key)

    @property
    def provider_enabled(self) -> bool:
        return self.client is not None

    @property
    def smart_model(self) -> str:
        return settings.openai_model_smart

    @property
    def fast_model(self) -> str:
        return settings.openai_model_fast

    def status(self) -> dict[str, Any]:
        return {
            "api_key_present": self.api_key_present,
            "smart_model": self.smart_model,
            "fast_model": self.fast_model,
            "provider_enabled": self.provider_enabled,
            "last_error": self.last_error,
        }

    def prompt(self, name: str) -> str:
        return (PROMPT_DIR / name).read_text(encoding="utf-8")

    async def json_task(
        self,
        prompt_name: str,
        payload: dict[str, Any],
        smart: bool = True,
    ) -> AIResult:
        """Call OpenAI for a JSON task, trying the smart model then the fast model.

        Returns an ``AIResult`` with ``ai_used=True`` on success. On failure it
        returns a deterministic fallback with a *sanitized* ``error`` reason so
        callers can tell the user exactly why AI was not used (missing key,
        model-not-found, or request failure) instead of a generic message.
        """
        if self.client is None:
            return AIResult(
                data=self._fallback(prompt_name, payload),
                model_used="deterministic-local",
                ai_used=False,
                error=_REASON_TEXT["no_key"],
                error_reason="no_key",
            )

        # Try the requested model, then the other model as a fallback.
        primary = self.smart_model if smart else self.fast_model
        secondary = self.fast_model if smart else self.smart_model
        models = [m for m in dict.fromkeys([primary, secondary]) if m]

        last_reason = "request"
        for model in models:
            try:
                data = await self._call(prompt_name, payload, model)
                self.last_error = None
                return AIResult(data=data, model_used=model, ai_used=True)
            except Exception as exc:  # noqa: BLE001 - classified below, never re-raised
                reason, sanitized = self._classify(exc)
                last_reason = reason
                # Log the sanitized reason only. Never log payload or the key.
                self.last_error = f"{model}: {sanitized}"
                logger.warning("OpenAI call failed (model=%s, reason=%s)", model, reason)

        return AIResult(
            data=self._fallback(prompt_name, payload),
            model_used="deterministic-local",
            ai_used=False,
            error=_REASON_TEXT.get(last_reason, _REASON_TEXT["request"]),
            error_reason=last_reason,
        )

    async def _call(self, prompt_name: str, payload: dict[str, Any], model: str) -> dict[str, Any]:
        response = await self.client.chat.completions.create(
            model=model,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": self.prompt(prompt_name)},
                {"role": "user", "content": json.dumps(payload, default=str)},
            ],
        )
        content = response.choices[0].message.content or "{}"
        return self._parse_json(content)

    @staticmethod
    def _classify(exc: Exception) -> tuple[str, str]:
        """Map an OpenAI/network exception to (reason_code, sanitized_message)."""
        if isinstance(exc, openai.NotFoundError):
            return "model_not_found", "model was not found"
        if isinstance(exc, (openai.AuthenticationError, openai.PermissionDeniedError)):
            return "auth", "authentication was rejected"
        if isinstance(exc, openai.RateLimitError):
            return "rate_limit", "rate limited"
        if isinstance(exc, (openai.APIConnectionError, openai.APITimeoutError)):
            return "request", "connection to the provider failed"
        if isinstance(exc, openai.BadRequestError):
            message = str(exc).lower()
            if "model" in message and ("not" in message or "does not exist" in message or "unknown" in message):
                return "model_not_found", "model was not found"
            return "request", "the provider rejected the request"
        if isinstance(exc, json.JSONDecodeError):
            return "request", "the provider returned invalid JSON"
        return "request", "the request failed"

    @staticmethod
    def _parse_json(content: str) -> dict[str, Any]:
        text = content.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if "\n" in text:
                text = text.split("\n", 1)[1]
        return json.loads(text)

    def _fallback(self, prompt_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        if prompt_name == "cover_letter.md":
            job = payload.get("job", {})
            profile = payload.get("profile", {})
            strengths = ", ".join((profile.get("skills") or [])[:3]) or "relevant project experience"
            return {
                "cover_letter": (
                    f"{profile.get('full_name', 'I')} is interested in the {job.get('title')} role at "
                    f"{job.get('company')} because it aligns with strengths in {strengths}. The attached "
                    "resume highlights directly supported experience and projects without adding "
                    "unsupported claims. I would welcome the chance to discuss how this background "
                    "can contribute to the team."
                )
            }
        if prompt_name == "application_answers.md":
            profile = payload.get("profile", {})
            return {
                "answers": {
                    "tell_us_about_yourself": f"I am a candidate focused on {', '.join(profile.get('target_roles') or ['software roles'])}.",
                    "sponsorship": "Please review my work authorization response in the profile.",
                    "relocation": "I am open to relocation." if profile.get("open_to_relocation") else "I prefer roles aligned with my listed locations.",
                }
            }
        return {"summary": "Local deterministic output generated without sending data to an AI provider."}


ai_provider = AIProvider()
