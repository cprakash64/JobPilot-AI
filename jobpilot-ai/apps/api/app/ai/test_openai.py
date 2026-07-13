"""Dev-only smoke test for the OpenAI provider.

    python -m app.ai.test_openai

Loads settings, checks the key is present, makes one tiny live call to the
configured smart model (falling back to the fast model), and prints success or a
sanitized error. It never prints the API key.
"""

from __future__ import annotations

import asyncio

from app.ai.provider import ai_provider


async def _run() -> int:
    status = ai_provider.status()
    print(f"OpenAI key present: {str(status['api_key_present']).lower()}")
    print(f"Smart model: {status['smart_model']}")
    print(f"Fast model: {status['fast_model']}")
    print(f"Provider enabled: {str(status['provider_enabled']).lower()}")

    if not status["provider_enabled"]:
        print("Test call: skipped (no API key configured)")
        return 1

    # A tiny JSON task exercising the real Chat Completions call + fallback chain.
    result = await ai_provider.json_task(
        "tailor_resume.md",
        {"profile": {"full_name": "Test", "skills": ["Python"]}, "experience": [], "projects": [],
         "job": {"title": "Software Engineer", "company": "Example", "description": "Build software."}},
        smart=True,
    )
    if result.ai_used:
        print(f"Test call: success (model={result.model_used})")
        return 0
    print(f"Test call: failed ({result.error})")
    if ai_provider.last_error:
        print(f"Last error: {ai_provider.last_error}")
    return 1


def main() -> None:
    raise SystemExit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
