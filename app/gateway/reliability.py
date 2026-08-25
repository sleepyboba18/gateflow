import random
import time

import requests

RETRYABLE_METHODS = {"GET", "HEAD", "OPTIONS"}
RETRYABLE_STATUSES = {502, 503, 504}


def request_with_retries(send, method: str, max_retries: int, backoff_ms: int, deadline: float):
    attempts = 0
    retries = 0
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise requests.Timeout("Gateway timeout budget exhausted")
        attempts += 1
        try:
            response = send(timeout=remaining)
            if response.status_code not in RETRYABLE_STATUSES or method.upper() not in RETRYABLE_METHODS or retries >= max_retries:
                return response, attempts, retries
        except (requests.Timeout, requests.ConnectionError):
            if method.upper() not in RETRYABLE_METHODS or retries >= max_retries:
                raise
        retries += 1
        delay = min((backoff_ms / 1000) * (2 ** (retries - 1)) + random.uniform(0, 0.025), max(deadline - time.monotonic(), 0))
        if delay <= 0:
            raise requests.Timeout("Gateway timeout budget exhausted")
        time.sleep(delay)
