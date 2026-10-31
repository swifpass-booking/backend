import json

from django.http import JsonResponse


def error(status, code, message):
    return JsonResponse({"error": {"code": code, "message": message}}, status=status)


def parse_body(request):
    """The JSON object in the request body ({} when empty), or None when malformed or not an object."""
    try:
        body = json.loads(request.body or "{}")
    except ValueError:
        return None
    return body if isinstance(body, dict) else None
