import httpx
import logging
from app.config import settings

logger = logging.getLogger(__name__)

class PseudoGramClient:
    def __init__(self, base_url: str = settings.PSEUDOGRAM_BASE_URL, api_key: str = settings.PSEUDOGRAM_API_KEY):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.headers = {
            "X-API-Key": self.api_key,
            "Content-Type": "application/json"
        }
        self.timeout = httpx.Timeout(10.0, connect=5.0)

    async def send_dm(self, recipient_user_id: str, message: str, comment_id: str, idempotency_key: str):
        url = f"{self.base_url}/v1/dm/send"
        payload = {
            "recipient_user_id": recipient_user_id,
            "message": message,
            "comment_id": comment_id
        }
        headers = {**self.headers, "Idempotency-Key": idempotency_key}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.post(url, json=payload, headers=headers)
                logger.info(f"DM API status: {response.status_code}")
                logger.info(f"DM API response: {response.text}")
                data = {}
                try:
                    data = response.json()
                except ValueError:
                    pass
                return response.status_code, data, response.headers
            except httpx.HTTPError as exc:
                logger.error(f"HTTP error during send_dm: {exc}")
                raise

    async def get_dm_status(self, dm_id: str):
        url = f"{self.base_url}/v1/dm/{dm_id}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.get(url, headers=self.headers)
                data = {}
                try:
                    data = response.json()
                except ValueError:
                    pass
                return response.status_code, data
            except httpx.HTTPError as exc:
                logger.error(f"HTTP error during get_dm_status: {exc}")
                raise
