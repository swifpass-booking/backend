import uuid


def prefixed_id(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:20]}"
