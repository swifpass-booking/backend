"""Authorization through OpenFGA (see openfga_model.json for the model).

Only who-holds-which-role facts are stored as tuples: `platform:main#admin@user:X` and
`provider:P#operator_staff|gate_agent@user:X`. The catalog structure (provider -> platform,
occurrence -> provider) is sent as contextual tuples on every call, so it never needs syncing.
Every check fails closed: if OpenFGA is unset or unreachable the answer is "no".
"""
import json
import logging
import urllib.error
import urllib.request
from pathlib import Path

from django.conf import settings

log = logging.getLogger(__name__)

PLATFORM = "platform:main"
STAFF_RELATIONS = ("operator_staff", "gate_agent")
MODEL = json.loads((Path(__file__).parent / "openfga_model.json").read_text())

_store_id = None


class AuthzError(Exception):
    pass


def _request(method, path, body=None):
    if not settings.OPENFGA_API_URL:
        raise AuthzError("OPENFGA_API_URL is not set")
    headers = {"Content-Type": "application/json"}
    if settings.OPENFGA_API_TOKEN:
        headers["Authorization"] = f"Bearer {settings.OPENFGA_API_TOKEN}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(settings.OPENFGA_API_URL.rstrip("/") + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        raise AuthzError(f"OpenFGA returned HTTP {e.code}: {e.read().decode(errors='replace')[:300]}")
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        raise AuthzError(f"Could not reach OpenFGA: {e}")


def _find_store():
    token = ""
    while True:
        page = _request("GET", "/stores?page_size=100" + (f"&continuation_token={token}" if token else ""))
        for s in page.get("stores", []):
            if s["name"] == settings.OPENFGA_STORE_NAME:
                return s["id"]
        token = page.get("continuation_token", "")
        if not token:
            return None


def store_id():
    global _store_id
    if settings.OPENFGA_STORE_ID:
        return settings.OPENFGA_STORE_ID
    if _store_id is None:
        _store_id = _find_store()
        if _store_id is None:
            raise AuthzError(f"OpenFGA store '{settings.OPENFGA_STORE_NAME}' not found (run manage.py openfga_setup)")
    return _store_id


def ensure_store_and_model():
    """Create the store if missing and write the current model. Idempotent; returns the store id."""
    global _store_id
    sid = settings.OPENFGA_STORE_ID or _find_store()
    if sid is None:
        sid = _request("POST", "/stores", {"name": settings.OPENFGA_STORE_NAME})["id"]
    _store_id = sid
    _request("POST", f"/stores/{sid}/authorization-models", MODEL)
    return sid


def _user(user_id):
    return f"user:{user_id}"


def _provider_tuples(provider_ids):
    return [{"user": PLATFORM, "relation": "platform", "object": f"provider:{p}"} for p in provider_ids]


def _all_provider_ids():
    from catalog.models import Provider

    return list(Provider.objects.values_list("id", flat=True))


def _check(user_id, relation, obj, contextual):
    body = {"tuple_key": {"user": _user(user_id), "relation": relation, "object": obj}}
    if contextual:
        body["contextual_tuples"] = {"tuple_keys": contextual}
    try:
        return bool(_request("POST", f"/stores/{store_id()}/check", body).get("allowed"))
    except AuthzError:
        log.exception("OpenFGA check failed; denying")
        return False


def is_admin(user_id):
    return _check(user_id, "admin", PLATFORM, [])


def _occurrence_check(user_id, relation, occurrence_id):
    from catalog.models import Occurrence

    provider_id = Occurrence.objects.filter(id=occurrence_id).values_list("service__provider_id", flat=True).first()
    if not provider_id:
        return False
    contextual = _provider_tuples([provider_id]) + [
        {"user": f"provider:{provider_id}", "relation": "provider", "object": f"occurrence:{occurrence_id}"}
    ]
    return _check(user_id, relation, f"occurrence:{occurrence_id}", contextual)


def can_scan(user_id, occurrence_id):
    return _occurrence_check(user_id, "can_scan", occurrence_id)


def can_track(user_id, occurrence_id):
    return _occurrence_check(user_id, "can_track", occurrence_id)


def staff_provider_ids(user_id):
    """Ids of every provider this user may operate (all of them for a platform admin)."""
    ids = _all_provider_ids()
    body = {
        "type": "provider",
        "relation": "staff",
        "user": _user(user_id),
        "contextual_tuples": {"tuple_keys": _provider_tuples(ids)},
    }
    try:
        objects = _request("POST", f"/stores/{store_id()}/list-objects", body).get("objects", [])
    except AuthzError:
        log.exception("OpenFGA list-objects failed; denying")
        return []
    return [o.split(":", 1)[1] for o in objects]


def _write(writes=(), deletes=()):
    body = {}
    if writes:
        body["writes"] = {"tuple_keys": list(writes)}
    if deletes:
        body["deletes"] = {"tuple_keys": list(deletes)}
    _request("POST", f"/stores/{store_id()}/write", body)


def _write_ignoring_existing(tuple_key):
    try:
        _write(writes=[tuple_key])
    except AuthzError as e:
        if "already exist" not in str(e):
            raise


def grant_admin(user):
    _write_ignoring_existing({"user": _user(user.id), "relation": "admin", "object": PLATFORM})
    _set_label(user, "admin")


def grant_staff(user, provider_id, relation):
    if relation not in STAFF_RELATIONS:
        raise ValueError(f"relation must be one of {STAFF_RELATIONS}")
    _write_ignoring_existing({"user": _user(user.id), "relation": relation, "object": f"provider:{provider_id}"})
    if user.role == "traveller":
        _set_label(user, relation)


def _set_label(user, role):
    # User.role is only a display label for the apps; OpenFGA is what authorizes.
    user.role = role
    user.save(update_fields=["role"])
