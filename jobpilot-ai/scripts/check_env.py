import os
import sys

REQUIRED = ["DATABASE_URL", "SECRET_KEY", "CORS_ORIGINS"]


def main() -> int:
    missing = [name for name in REQUIRED if not os.getenv(name)]
    if missing:
        print(f"Missing required environment variables: {', '.join(missing)}")
        return 1
    if os.getenv("SECRET_KEY") in {"change-me-in-production", "dev-only-change-me"}:
        print("SECRET_KEY must be changed for production.")
        return 1
    print("Environment looks valid.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

