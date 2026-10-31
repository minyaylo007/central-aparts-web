"""
AWS Lambda WSGI Adapter for Flask.
Handles Lambda Function URL events.
"""
import base64
import io
import sys
from typing import Any

from app import app


def lambda_handler(event: dict, context: Any) -> dict:
    if event.get("warming"):
        return {"statusCode": 200, "body": "warm"}

    request_context = event.get("requestContext", {})
    http_context = request_context.get("http", {})

    method       = http_context.get("method", "GET")
    path         = event.get("rawPath", "/")
    query_string = event.get("rawQueryString", "")
    headers      = event.get("headers", {})

    body = event.get("body", "")
    if event.get("isBase64Encoded") and body:
        body = base64.b64decode(body)
    elif body:
        body = body.encode("utf-8")
    else:
        body = b""

    environ = {
        "REQUEST_METHOD":  method,
        "SCRIPT_NAME":     "",
        "PATH_INFO":       path,
        "QUERY_STRING":    query_string,
        "CONTENT_TYPE":    headers.get("content-type", ""),
        "CONTENT_LENGTH":  str(len(body)),
        "SERVER_NAME":     headers.get("host", "localhost"),
        "SERVER_PORT":     "443",
        "SERVER_PROTOCOL": "HTTP/1.1",
        "wsgi.version":    (1, 0),
        "wsgi.url_scheme": "https",
        "wsgi.input":      io.BytesIO(body),
        "wsgi.errors":     sys.stderr,
        "wsgi.multithread":  False,
        "wsgi.multiprocess": False,
        "wsgi.run_once":     True,
    }

    for key, value in headers.items():
        k = key.upper().replace("-", "_")
        if k not in ("CONTENT_TYPE", "CONTENT_LENGTH"):
            environ[f"HTTP_{k}"] = value

    if "x-forwarded-for" in headers:
        environ["REMOTE_ADDR"] = headers["x-forwarded-for"].split(",")[0].strip()
    else:
        environ["REMOTE_ADDR"] = request_context.get("http", {}).get("sourceIp", "127.0.0.1")

    response_headers = []
    status_code = 200

    def start_response(status, hdrs, exc_info=None):
        nonlocal status_code, response_headers
        status_code = int(status.split(" ")[0])
        response_headers = hdrs
        return lambda s: None

    response_body = b""
    try:
        for chunk in app.wsgi_app(environ, start_response):
            response_body += chunk
    except Exception:
        return {
            "statusCode": 500,
            "headers": {"Content-Type": "text/html; charset=utf-8"},
            "body": "<h1>500 Internal Server Error</h1>",
            "isBase64Encoded": False,
        }

    headers_dict = {k: v for k, v in response_headers}
    content_type = headers_dict.get("Content-Type", "")
    is_binary = not (
        content_type.startswith("text/")
        or content_type.startswith("application/json")
        or content_type.startswith("application/xml")
    )

    return {
        "statusCode":     status_code,
        "headers":        headers_dict,
        "body":           base64.b64encode(response_body).decode() if is_binary
                          else response_body.decode("utf-8"),
        "isBase64Encoded": is_binary,
    }
