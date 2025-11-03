from dataclasses import dataclass
from typing import Dict, Any

@dataclass
class ProviderCredentials:
    api_key: str
    api_secret: str
    passphrase: str | None = None
    extras: Dict[str, Any] | None = None

class ProviderBase:
    id: str
    name: str
    def __init__(self, creds: ProviderCredentials):
        self.creds = creds

    async def ping(self) -> bool:
        return True  # override with real API calls

    async def balances(self) -> dict:
        return {}    # override
