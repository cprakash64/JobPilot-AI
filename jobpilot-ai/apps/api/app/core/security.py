import base64
import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta, timezone

try:
    from jose import JWTError, jwt
except ModuleNotFoundError:
    JWTError = Exception
    jwt = None

try:
    from passlib.context import CryptContext
except ModuleNotFoundError:
    CryptContext = None

from app.core.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto") if CryptContext else None
ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    if pwd_context:
        return pwd_context.hash(password)
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 310_000).hex()
    return f"pbkdf2_sha256${salt}${digest}"


def verify_password(password: str, hashed_password: str) -> bool:
    if pwd_context:
        return pwd_context.verify(password, hashed_password)
    try:
        scheme, salt, digest = hashed_password.split("$", 2)
    except ValueError:
        return False
    if scheme != "pbkdf2_sha256":
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 310_000).hex()
    return hmac.compare_digest(candidate, digest)


def create_access_token(subject: str) -> str:
    expires = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expires_minutes)
    payload = {"sub": subject, "exp": expires}
    if jwt:
        return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)
    return _encode_hs256({**payload, "exp": int(expires.timestamp())})


def decode_access_token(token: str) -> str | None:
    if jwt:
        try:
            payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
        except JWTError:
            return None
    else:
        payload = _decode_hs256(token)
        if payload is None:
            return None
    subject = payload.get("sub")
    return subject if isinstance(subject, str) else None


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _encode_hs256(payload: dict) -> str:
    header = {"alg": ALGORITHM, "typ": "JWT"}
    signing_input = ".".join(
        [
            _b64url(json.dumps(header, separators=(",", ":")).encode()),
            _b64url(json.dumps(payload, separators=(",", ":")).encode()),
        ]
    )
    signature = hmac.new(settings.secret_key.encode(), signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{_b64url(signature)}"


def _decode_hs256(token: str) -> dict | None:
    try:
        header_segment, payload_segment, signature_segment = token.split(".")
        signing_input = f"{header_segment}.{payload_segment}"
        expected = hmac.new(settings.secret_key.encode(), signing_input.encode(), hashlib.sha256).digest()
        actual = _b64url_decode(signature_segment)
        if not hmac.compare_digest(expected, actual):
            return None
        payload = json.loads(_b64url_decode(payload_segment))
        if int(payload.get("exp", 0)) < int(datetime.now(timezone.utc).timestamp()):
            return None
        return payload
    except (ValueError, json.JSONDecodeError, TypeError):
        return None
