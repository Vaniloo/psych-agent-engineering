"""Short-lived, request-bound application approval after model gate agreement."""

import base64
import hashlib
import hmac
import json
import time


def sign_approval(secret, model_sha, owner, session_id, request_id, message, operation, selection=None):
    if not secret or len(model_sha) != 64 or operation not in {"create", "progress", "revise", "complete", "pause"}:
        raise ValueError("configured model approval required")
    body = {"contract": "psych-goal-gate-v1", "modelSha": model_sha, "owner": owner,
            "sessionId": session_id, "requestId": request_id, "operation": operation,
            "sourceSha": hashlib.sha256(message.encode()).hexdigest(), "expires": int(time.time()) + 300}
    if selection:
        body.update(selection)
    payload = base64.urlsafe_b64encode(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).decode().rstrip("=")
    signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return payload + "." + signature
