import os
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import Flask, g, jsonify, request
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.exc import IntegrityError

from claimer import start as start_claimer
from models import (
    Base,
    ConvergenceLog,
    SectionStake,
    SessionLocal,
    engine,
    row_dict,
    stake_dict,
)

SECRET = os.environ.get("JWT_SECRET", "tunnelconv-dev-secret")
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
USERS = {
    "surveyor": {"role": "writer", "password_hash": pwd.hash("surv123456")},
    "inspector": {"role": "reader", "password_hash": pwd.hash("insp123456")},
}

RETURN_NO_STAKE = "还没钉桩：该断面未钉进坐标册，整份退回，请先钉桩再报送"

app = Flask(__name__)


def seed():
    Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        if db.query(ConvergenceLog).count() > 0:
            return
        now = datetime.now(timezone.utc)
        seeds = (
            ("K12+180", "X1200.500 / Y800.200", 1.2, "合格"),
            ("K18+040", "X1804.000 / Y902.750", 5.6, "超限"),
        )
        for chainage, coordinate, delta, expect in seeds:
            from rules import judge

            verdict, reason = judge(delta)
            assert verdict == expect
            db.add(
                SectionStake(
                    chainage=chainage,
                    coordinate=coordinate,
                    staked_by="surveyor",
                    staked_at=now,
                    updated_at=now,
                )
            )
            db.add(
                ConvergenceLog(
                    chainage=chainage,
                    delta_mm=delta,
                    coordinate_snapshot=coordinate,
                    status="done",
                    verdict=verdict,
                    reason=reason,
                    created_by="surveyor",
                    created_at=now,
                    processed_at=now,
                )
            )
        db.commit()
    finally:
        db.close()


seed()
start_claimer()


def current_user():
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    try:
        payload = jwt.decode(auth[7:].strip(), SECRET, algorithms=["HS256"])
    except JWTError:
        return None
    sub = payload.get("sub")
    if sub not in USERS:
        return None
    return {"username": sub, "role": payload.get("role")}


def require_login(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = current_user()
        if user is None:
            return jsonify({"detail": "未登录"}), 401
        g.user = user
        return fn(*args, **kwargs)

    return wrapper


def require_writer(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = current_user()
        if user is None:
            return jsonify({"detail": "未登录"}), 401
        if user["role"] != "writer":
            return jsonify({"detail": "巡检员为只读权限，不能钉桩、改坐标或报送"}), 403
        g.user = user
        return fn(*args, **kwargs)

    return wrapper


@app.get("/api/health")
def health():
    return jsonify({"status": "ok", "service": "tunnel-convergence-desk"})


@app.post("/api/auth/login")
def login():
    body = request.get_json(silent=True) or {}
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    user = USERS.get(username)
    if not user or not pwd.verify(password, user["password_hash"]):
        return jsonify({"detail": "用户名或密码错误"}), 401
    exp = datetime.now(timezone.utc) + timedelta(hours=8)
    token = jwt.encode(
        {"sub": username, "role": user["role"], "exp": exp}, SECRET, algorithm="HS256"
    )
    return jsonify({"access_token": token, "username": username, "role": user["role"]})


@app.get("/api/stakes")
@require_login
def list_stakes():
    db = SessionLocal()
    try:
        rows = db.query(SectionStake).order_by(SectionStake.id.desc()).all()
        return jsonify([stake_dict(r) for r in rows])
    finally:
        db.close()


@app.get("/api/logs")
@require_login
def list_logs():
    db = SessionLocal()
    try:
        rows = db.query(ConvergenceLog).order_by(ConvergenceLog.id.desc()).all()
        return jsonify([row_dict(r) for r in rows])
    finally:
        db.close()


@app.post("/api/submissions")
@require_writer
def submit():
    """钉桩登记 + 收敛报送必须同一次提交、同一事务完成。

    - 缺坐标（没钉桩）：整份退回，落一条 returned 单据，写明还没钉桩。
    - 坐标齐全：把断面钉进册（唯一），并把坐标快照抄进本单据后进待认领。
    - 两人抢同一新桩号：唯一约束下负方立刻 409，名下不留任何单。
    """
    body = request.get_json(silent=True) or {}
    chainage = (body.get("chainage") or "").strip()
    coordinate = (body.get("coordinate") or "").strip()
    if not chainage:
        return jsonify({"detail": "断面号（桩号）不能为空"}), 400
    try:
        delta_mm = float(body.get("delta_mm"))
    except (TypeError, ValueError):
        return jsonify({"detail": "收敛值必须是数字"}), 400

    now = datetime.now(timezone.utc)
    username = g.user["username"]
    db = SessionLocal()
    try:
        # 没钉桩就报送：整份退回，登记退单原因，进待钉清单。
        if not coordinate:
            returned = ConvergenceLog(
                chainage=chainage,
                delta_mm=delta_mm,
                coordinate_snapshot=None,
                status="returned",
                verdict=None,
                reason=RETURN_NO_STAKE,
                created_by=username,
                created_at=now,
                processed_at=None,
            )
            db.add(returned)
            db.commit()
            db.refresh(returned)
            return jsonify(row_dict(returned)), 202

        # 已钉档案加行锁，保证快照读取与并发提交串行化。
        stake = (
            db.query(SectionStake)
            .filter(SectionStake.chainage == chainage)
            .with_for_update()
            .first()
        )
        if stake is None:
            stake = SectionStake(
                chainage=chainage,
                coordinate=coordinate,
                staked_by=username,
                staked_at=now,
                updated_at=now,
            )
            db.add(stake)
            try:
                db.flush()  # 触发 uq_stake_chainage；并发抢桩负方在此失败
            except IntegrityError:
                db.rollback()
                return (
                    jsonify(
                        {
                            "detail": f"桩号 {chainage} 已被另一单抢先钉走，本单立刻作废"
                        }
                    ),
                    409,
                )

        # 钉桩成功：坐标以档案为准抄进本单据，随后进待认领。
        log = ConvergenceLog(
            chainage=chainage,
            delta_mm=delta_mm,
            coordinate_snapshot=stake.coordinate,
            status="pending",
            verdict=None,
            reason=None,
            created_by=username,
            created_at=now,
            processed_at=None,
        )
        db.add(log)
        db.commit()
        db.refresh(log)
        return jsonify(row_dict(log)), 201
    finally:
        db.close()


@app.patch("/api/stakes/<int:stake_id>")
@require_writer
def update_stake(stake_id):
    """只改坐标册上的当前坐标；绝不回写任何单据的坐标快照或已办结结论。"""
    body = request.get_json(silent=True) or {}
    coordinate = (body.get("coordinate") or "").strip()
    if not coordinate:
        return jsonify({"detail": "坐标不能为空"}), 400
    db = SessionLocal()
    try:
        stake = db.get(SectionStake, stake_id)
        if stake is None:
            return jsonify({"detail": "桩号档案不存在"}), 404
        stake.coordinate = coordinate
        stake.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(stake)
        return jsonify(stake_dict(stake))
    finally:
        db.close()
