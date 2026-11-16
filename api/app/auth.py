"""Who may call the review endpoints.

Production sits behind Cloudflare Access: every request carries a signed `Cf-Access-Jwt-Assertion`
header. We verify it against the team's public keys and the application's AUD tag, then check the
email against REVIEWER_EMAILS, so the API stays closed even if someone reaches it around Access.
The agent token is also accepted, for scripts and tests.
"""

import secrets
from functools import lru_cache

import jwt
from fastapi import Header, HTTPException

from app.config import settings


@lru_cache(maxsize=1)
def _jwks() -> jwt.PyJWKClient:
    return jwt.PyJWKClient(f"https://{settings.cf_access_team_domain}/cdn-cgi/access/certs", cache_keys=True)


def access_email(token: str) -> str:
    key = _jwks().get_signing_key_from_jwt(token)
    claims = jwt.decode(
        token,
        key.key,
        algorithms=["RS256"],
        audience=settings.cf_access_aud,
        issuer=f"https://{settings.cf_access_team_domain}",
    )
    return (claims.get("email") or "").lower()


def require_reviewer(
    cf_access_jwt_assertion: str = Header(default=""),
    authorization: str = Header(default=""),
) -> str:
    bearer = authorization.removeprefix("Bearer ").strip()
    if bearer and secrets.compare_digest(bearer, settings.agent_token):
        return "agent"
    if settings.review_auth == "none":
        return "local"
    if not (settings.cf_access_team_domain and settings.cf_access_aud):
        raise HTTPException(503, "Cloudflare Access is not configured on the API")
    if not cf_access_jwt_assertion:
        raise HTTPException(401, "missing Cloudflare Access token")
    try:
        email = access_email(cf_access_jwt_assertion)
    except jwt.PyJWTError as e:
        raise HTTPException(401, f"bad Cloudflare Access token: {e}") from None
    if email not in {e.lower() for e in settings.reviewer_emails}:
        raise HTTPException(403, "not on the reviewer allowlist")
    return email
