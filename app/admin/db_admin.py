# app/admin/db_admin.py
from __future__ import annotations

from datetime import datetime, date
from urllib.parse import urlencode

from flask import request, render_template, jsonify, abort
from flask_login import login_required
from sqlalchemy.sql import sqltypes as T

from app.extensions import db
from app.models import (
    Game, Pick, User, TeamGameATS, WeeklyEmailLog, WeeklyEmailRecipientLog,
    Season, UserSeason, ChatMessage,
)

from . import bp

# ------------------------------------------------------------
# DB Manager
# ------------------------------------------------------------
MODEL_MAP = {
    "users": User,
    "games": Game,
    "picks": Pick,
    "ats": TeamGameATS,
    "email_log": WeeklyEmailLog,
    "email_recipients": WeeklyEmailRecipientLog,
    "seasons": Season,
    "user_seasons": UserSeason,
    "chat_messages": ChatMessage,
}

def _get_model_or_404(name: str):
    m = MODEL_MAP.get(name.lower())
    if not m:
        abort(404)
    return m

@bp.get("/db")
@login_required
def db_home():
    return render_template("db_home.html", model_names=sorted(MODEL_MAP.keys()))

@bp.get("/db/<model_name>")
@login_required
def db_table(model_name):
    Model = _get_model_or_404(model_name)
    page = max(1, int(request.args.get("page", 1)))
    per_page = min(100, int(request.args.get("per_page", 25)))

    # All columns + primary key
    cols = [c.name for c in Model.__table__.columns]
    pk = list(Model.__table__.primary_key.columns)[0].name

    # -------- column kind detection (for rendering + parsing) --------
    def kind_for(col):
        t = col.type
        if isinstance(t, (T.String, T.Text, T.Unicode, T.UnicodeText)):
            return "text"
        if isinstance(t, T.Boolean):
            return "bool"
        if isinstance(t, (T.Integer, T.BigInteger, T.SmallInteger)):
            return "int"
        if isinstance(t, (T.Numeric, T.Float, T.DECIMAL)):
            return "num"
        if isinstance(t, T.DateTime):
            return "dt"
        if isinstance(t, T.Date):
            return "date"
        return "other"

    col_kinds = {c.name: kind_for(c) for c in Model.__table__.columns}

    # -------- base query + global search --------
    query = Model.query
    q = request.args.get("q")
    if q:
        like = f"%{q}%"
        or_clauses = []
        for c in Model.__table__.columns:
            if col_kinds[c.name] == "text":
                or_clauses.append(getattr(Model, c.name).ilike(like))
        if or_clauses:
            from sqlalchemy import or_
            query = query.filter(or_(*or_clauses))

    # -------- per-column filters (f_<col>, f_<col>_min, f_<col>_max) --------
    def parse_bool(v: str) -> bool | None:
        if v is None or v == "":
            return None
        v = str(v).strip().lower()
        if v in ("1", "true", "t", "yes", "y", "on"):
            return True
        if v in ("0", "false", "f", "no", "n", "off"):
            return False
        return None

    fvals = {}
    for c in cols:
        k = col_kinds[c]
        col = getattr(Model, c)

        val  = request.args.get(f"f_{c}", "")
        vmin = request.args.get(f"f_{c}_min", "")
        vmax = request.args.get(f"f_{c}_max", "")

        fvals[c] = {"kind": k, "val": val, "min": vmin, "max": vmax}

        if k == "text":
            if val:
                query = query.filter(col.ilike(f"%{val}%"))
        elif k in ("int", "num"):
            # exact value if provided
            if val:
                try:
                    n = float(val) if k == "num" else int(val)
                    query = query.filter(col == n)
                except Exception:
                    pass
            # range if provided
            if vmin:
                try:
                    n = float(vmin) if k == "num" else int(vmin)
                    query = query.filter(col >= n)
                except Exception:
                    pass
            if vmax:
                try:
                    n = float(vmax) if k == "num" else int(vmax)
                    query = query.filter(col <= n)
                except Exception:
                    pass
        elif k == "bool":
            b = parse_bool(val)
            if b is not None:
                # .is_(True/False) is correct for boolean
                query = query.filter(col.is_(b))
        elif k in ("dt", "date"):
            def _parse_dt(s: str):
                if not s:
                    return None
                try:
                    return datetime.fromisoformat(s)
                except Exception:
                    return None
            def _parse_d(s: str):
                if not s:
                    return None
                try:
                    return date.fromisoformat(s)
                except Exception:
                    return None

            if k == "dt":
                dmin = _parse_dt(vmin); dmax = _parse_dt(vmax)
            else:
                dmin = _parse_d(vmin);  dmax = _parse_d(vmax)

            if dmin is not None:
                query = query.filter(col >= dmin)
            if dmax is not None:
                query = query.filter(col <= dmax)
        else:
            # unknown types: fall back to substring match if value provided
            if val:
                try:
                    query = query.filter(col.ilike(f"%{val}%"))
                except Exception:
                    pass

    # -------- sorting --------
    sort = request.args.get("sort") or pk
    dir_ = request.args.get("dir", "asc").lower()
    if sort not in cols:
        sort = pk
    col_obj = getattr(Model, sort)
    if dir_ == "desc":
        query = query.order_by(col_obj.desc())
    else:
        dir_ = "asc"
        query = query.order_by(col_obj.asc())

    # paginate AFTER filtering/sorting
    page_obj = query.paginate(page=page, per_page=per_page, error_out=False)
    rows = page_obj.items

    # build a preserved query string without sort/dir/page (for header links)
    preserved = {k: v for k, v in request.args.items()}
    for k in ("sort", "dir", "page"):
        preserved.pop(k, None)
    base_qs = urlencode(preserved)

    return render_template(
        "db_table.html",
        model_name=model_name,
        cols=cols,
        rows=rows,
        pk=pk,
        page=page_obj.page,
        pages=page_obj.pages or 1,
        per_page=per_page,
        q=q or "",
        sort=sort,
        dir=dir_,
        col_kinds=col_kinds,
        fvals=fvals,
        base_qs=base_qs,
    )


@bp.patch("/db/<model_name>/<int:row_id>")
@login_required
def db_update_cell(model_name, row_id):
    data = request.get_json(force=True, silent=True) or {}
    field = data.get("field"); value = data.get("value")
    if not field:
        return jsonify({"ok": False, "error": "Missing field"}), 400

    Model = _get_model_or_404(model_name)
    obj = Model.query.get_or_404(row_id)
    if field not in Model.__table__.columns:
        return jsonify({"ok": False, "error": f"Unknown field '{field}'"}), 400

    col = Model.__table__.columns[field]
    try:
        pytype = col.type.python_type
        if value is None or value == "":
            casted = None
        elif pytype is bool:
            casted = str(value).lower() in ("1","true","t","yes","y","on")
        else:
            casted = pytype(value)
    except Exception:
        casted = value

    setattr(obj, field, casted)
    db.session.add(obj)
    db.session.commit()
    return jsonify({"ok": True})
