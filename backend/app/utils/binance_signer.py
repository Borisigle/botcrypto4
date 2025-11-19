"""Binance API request signer using HMAC-SHA256."""
import hashlib
import hmac
import time
from typing import Dict
from urllib.parse import urlencode


class BinanceSigner:
    """Signs Binance API requests with HMAC-SHA256 signature."""

    def __init__(self, api_key: str, api_secret: str) -> None:
        """Initialize signer with API credentials.
        
        Args:
            api_key: Binance API key
            api_secret: Binance API secret
        """
        self.api_key = api_key
        self.api_secret = api_secret

    def sign_request(self, params: Dict[str, any]) -> Dict[str, any]:
        """Create HMAC-SHA256 signature for Binance API request.
        
        Args:
            params: Request parameters to sign
            
        Returns:
            Dictionary with added timestamp and signature
        """
        params["timestamp"] = int(time.time() * 1000)

        query_string = urlencode(params)
        signature = hmac.new(
            self.api_secret.encode(),
            query_string.encode(),
            hashlib.sha256,
        ).hexdigest()

        params["signature"] = signature
        return params
