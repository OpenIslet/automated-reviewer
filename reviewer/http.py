"""One JSON request to a model provider, over HTTPS, with nothing logged.

Used by the adapters that talk to a provider's REST API directly. It returns the status and the parsed body, for an
error as well as for a success, because some providers report a blocked request in the body of an error.
"""
import http.client
import json
import urllib.error
import urllib.request
from typing import Dict, Optional, Tuple

TIMEOUT_SECONDS = 1800  # a panel model thinking at its deepest setting can take many minutes to answer


class Http:
    def post(self, url: str, headers: Dict[str, str], body: dict, timeout: int = TIMEOUT_SECONDS) -> Tuple[int, Optional[dict]]:
        """(status, parsed JSON body or None). Status 0 means the provider could not be reached, or did not answer in time."""
        if not url.startswith("https://"):
            raise ValueError("only HTTPS addresses are used")
        request = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST", headers=dict(headers, **{"Content-Type": "application/json"}))
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.status, _parsed(response.read())
        except urllib.error.HTTPError as error:
            return error.code, _parsed(error.read())
        except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError):  # includes an answer cut short
            return 0, None


def _parsed(raw: bytes) -> Optional[dict]:
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def is_passing(status: int) -> bool:
    """A fault worth trying again: unreachable, timed out, rate limited, or the provider's own fault."""
    return status == 0 or status == 429 or status >= 500


def error_code(body: Optional[dict]) -> Optional[str]:
    """The provider's short code for an error, when the body has one.

    Most providers put a word in `error.code`. Google's older endpoints, the token count among them, put a number
    there. The word is then in `error.status`, and the exact reason (a refused key, billing switched off) is in
    `error.details`. The reason is the more useful of the two, so it is looked for first.
    """
    error = (body or {}).get("error")
    if not isinstance(error, dict):
        return None
    code = error.get("code")
    if isinstance(code, str):
        return code
    for detail in error.get("details") if isinstance(error.get("details"), list) else []:
        reason = detail.get("reason") if isinstance(detail, dict) else None
        if isinstance(reason, str):
            return reason
    status = error.get("status")
    return status if isinstance(status, str) else None
