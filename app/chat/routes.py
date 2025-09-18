from flask import jsonify, request, abort
from flask_login import login_required, current_user
from datetime import datetime, timezone

from . import bp
from app.extensions import db
from app.models import ChatMessage


def _iso_timestamp(dt):
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat().replace('+00:00', 'Z')


def _serialize_message(message: ChatMessage) -> dict:
    user = getattr(message, 'user', None)
    display_name = None
    if user is not None:
        display_name = getattr(user, 'display_full_name', None) or getattr(user, 'username', None)
    return {
        "id": message.id,
        "user": {
            "id": getattr(user, 'id', None),
            "display_name": display_name or "Member",
        },
        "body": message.body,
        "created_at": _iso_timestamp(message.created_at),
        "updated_at": _iso_timestamp(message.updated_at),
    }


@bp.get("/messages")
@login_required
def list_messages():
    """Return recent chat messages (latest first)."""
    limit = request.args.get("limit", type=int) or 50
    limit = max(1, min(limit, 200))

    query = (
        ChatMessage.query
        .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
        .limit(limit)
    )
    messages = list(reversed(query.all()))
    return jsonify({"messages": [_serialize_message(m) for m in messages]})


@bp.post("/messages")
@login_required
def post_message():
    """Append a new chat message to the database."""
    data = request.get_json(silent=True) or {}
    body = (data.get("body") or "").strip()
    if not body:
        abort(400, description="Message body required")
    if len(body) > 1000:
        abort(400, description="Message too long")

    message = ChatMessage()
    message.body = body
    message.user = current_user
    db.session.add(message)
    db.session.commit()

    return jsonify(_serialize_message(message)), 201


@bp.patch("/messages/<int:message_id>")
@login_required
def update_message(message_id: int):
    """Edit an existing chat message owned by the current user."""
    message = ChatMessage.query.get_or_404(message_id)
    if message.user_id != current_user.id:
        abort(403, description="You can only edit your own messages")

    data = request.get_json(silent=True) or {}
    body = (data.get("body") or "").strip()
    if not body:
        abort(400, description="Message body required")
    if len(body) > 1000:
        abort(400, description="Message too long")

    message.body = body
    message.updated_at = datetime.now(timezone.utc)
    db.session.commit()

    return jsonify(_serialize_message(message)), 200
