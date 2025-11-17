import json
import textwrap
import sys
from logging import Logger
from requests import Response
from requests.exceptions import HTTPError


def log_http_error(message: str, logger: Logger, response: Response, http_err: HTTPError) -> None:
    # Safely attempt to decode JSON response; fall back to text when unavailable
    try:
        body = response.json()
        body_str = json.dumps(body, indent=4)
    except Exception:
        body_str = getattr(response, 'text', '<no response body>')

    logger.error(message + ':\n' + textwrap.indent(f'{http_err}\n{body_str}', '\t'))
    # Do not exit the process here; allow callers to handle fatal conditions.
    return
