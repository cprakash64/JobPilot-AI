"""Clear only People-provider circuit-breaker state.

Circuit state now lives in Redis under a versioned namespace, so restarting the
API does **not** clear it. This script deletes exactly the keys under
``people:circuit:<version>:`` and nothing else — it never issues FLUSHALL or
touches unrelated Redis data.

Usage:

    python -m scripts.clear_people_circuit_state --dry-run
    python -m scripts.clear_people_circuit_state
    python -m scripts.clear_people_circuit_state --provider pdl
"""

from __future__ import annotations

import argparse
import json

from app.core.config import settings
from app.people.circuit import (
    CIRCUIT_KEY_PREFIX,
    CIRCUIT_NAMESPACE_VERSION,
    clear_people_circuits,
)


def _scan_keys(pattern: str) -> list[str]:
    import redis

    client = redis.Redis.from_url(
        settings.redis_url, socket_connect_timeout=2, socket_timeout=2
    )
    found: list[str] = []
    cursor = 0
    while True:
        cursor, keys = client.scan(cursor=cursor, match=pattern, count=200)
        found.extend(
            key.decode() if isinstance(key, bytes) else str(key) for key in keys
        )
        if cursor == 0:
            return found


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--provider",
        help="Limit to one provider (for example 'pdl' or 'apollo').",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List the matching keys without deleting anything.",
    )
    args = parser.parse_args()

    pattern = (
        f"{CIRCUIT_KEY_PREFIX}:{args.provider}:*"
        if args.provider
        else f"{CIRCUIT_KEY_PREFIX}:*"
    )
    if args.dry_run:
        keys = _scan_keys(pattern)
        print(
            json.dumps(
                {
                    "namespace_version": CIRCUIT_NAMESPACE_VERSION,
                    "pattern": pattern,
                    "matched": len(keys),
                    "keys": sorted(keys)[:100],
                },
                indent=2,
            )
        )
        return

    removed = clear_people_circuits(provider=args.provider)
    print(
        json.dumps(
            {
                "namespace_version": CIRCUIT_NAMESPACE_VERSION,
                "pattern": pattern,
                "removed": removed,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
