from httpx import AsyncClient
from app.core.config import config
from app.utils.log import log

class PasarguardAuthService:
    def __init__(self):
        if config is None:
            raise RuntimeError("Configuration is not loaded")
        self.api_key = config.pasarguard.pasarguard_api_key.get_secret_value()
        self.base_url = config.pasarguard.pasarguard_admin_panel
    
    async def validate_api_key(self) -> bool:
        if not self.api_key:
            log.warning("Pasarguard API key is not set.")
            return False
        
        url = f"{self.base_url}/api/admin"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        
        try:
            async with AsyncClient() as client:
                response = await client.get(url, headers=headers, timeout=10)
                response.raise_for_status()
                data = response.json()
                is_valid = data.get("valid", False)
                log.debug(f"Pasarguard API key validation response: {data}")
                return is_valid
        except Exception as e:
            log.error(f"Error validating Pasarguard API key: {e}")
            return False
