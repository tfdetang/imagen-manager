"""API Key authentication middleware."""
from fastapi import HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings

security = HTTPBearer()


async def verify_api_key(
    credentials: HTTPAuthorizationCredentials = Security(security),
) -> str:
    """
    Verify API key from Authorization header.

    Args:
        credentials: HTTP Bearer credentials

    Returns:
        The API key if valid

    Raises:
        HTTPException: If API key is invalid
    """
    if credentials.credentials != settings.api_key:
        raise HTTPException(
            status_code=401,
            detail={
                "error": {
                    "message": "Invalid API key",
                    "type": "authentication_error",
                    "code": "invalid_api_key",
                }
            },
        )

    return credentials.credentials
