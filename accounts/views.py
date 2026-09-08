import functools
import json
import re

from django.db.models import Q
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .jwt import decode_token, issue_token
from .models import User

EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
PHONE_RE = re.compile(r"^\+?\d{7,15}$")


def is_valid_email(value):
    return bool(EMAIL_RE.match(value or ""))


def is_valid_phone(value):
    return bool(PHONE_RE.match(value or ""))


def error(status, code, message):
    return JsonResponse({"error": {"code": code, "message": message}}, status=status)


def parse_body(request):
    if not request.body:
        return {}
    try:
        return json.loads(request.body)
    except json.JSONDecodeError:
        return {}


def require_auth(view):
    @functools.wraps(view)
    def wrapped(request, *args, **kwargs):
        header = request.headers.get("Authorization", "")
        token = header[7:] if header.startswith("Bearer ") else None
        if not token:
            return error(401, "unauthorised", "Sign in required.")
        user_id = decode_token(token)
        if not user_id:
            return error(401, "unauthorised", "Session expired — sign in again.")
        request.user_id = user_id
        return view(request, *args, **kwargs)

    return wrapped


def require_admin(view):
    @functools.wraps(view)
    @require_auth
    def wrapped(request, *args, **kwargs):
        user = User.objects.filter(id=request.user_id).first()
        if not user or user.role != "admin":
            return error(403, "unauthorised", "Admin access required.")
        request.admin_user = user
        return view(request, *args, **kwargs)

    return wrapped


@csrf_exempt
@require_http_methods(["POST"])
def register(request):
    body = parse_body(request)
    full_name = body.get("fullName")
    identifier = (body.get("identifier") or "").strip()
    password = body.get("password")

    if not full_name or not isinstance(full_name, str) or len(full_name.strip()) < 2:
        return error(400, "validation_failed", "Full name is required.")
    if not password or not isinstance(password, str) or len(password) < 8:
        return error(400, "validation_failed", "Password must be at least 8 characters.")

    is_email = is_valid_email(identifier)
    is_phone = is_valid_phone(identifier)
    if not is_email and not is_phone:
        return error(400, "validation_failed", "Enter a valid email or phone number.")

    lookup = Q(email=identifier) if is_email else Q(phone_e164=identifier)
    if User.objects.filter(lookup).exists():
        return error(409, "idempotency_conflict", "An account with that email or phone already exists.")

    user = User(
        email=identifier if is_email else None,
        phone_e164=identifier if is_phone else None,
        full_name=full_name.strip(),
    )
    user.set_password(password)
    user.save()

    token = issue_token(user.id)
    return JsonResponse({"token": token, "user": user.to_public_dict()}, status=201)


@csrf_exempt
@require_http_methods(["POST"])
def login(request):
    body = parse_body(request)
    identifier = (body.get("identifier") or "").strip()
    password = body.get("password")

    if not identifier or not password:
        return error(400, "validation_failed", "Enter your email/phone and password.")

    user = User.objects.filter(Q(email=identifier) | Q(phone_e164=identifier)).first()
    if not user or not user.check_password(password):
        return error(401, "unauthorised", "Incorrect email/phone or password.")

    token = issue_token(user.id)
    return JsonResponse({"token": token, "user": user.to_public_dict()})


@require_http_methods(["GET"])
@require_auth
def me(request):
    user = User.objects.filter(id=request.user_id).first()
    if not user:
        return error(401, "unauthorised", "Account no longer exists.")
    return JsonResponse({"user": user.to_public_dict()})
