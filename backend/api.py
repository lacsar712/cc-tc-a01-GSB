import os
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import Flask, g, jsonify, request
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from claimer import start as start_claimer
from models import Base, ConvergenceLog, Stake, SessionLocal, engine, row_dict, stake_dict
from rules import judge

SECRET = os.environ.get("JWT_SECRET", "tunnelconv-dev-secret")
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
USERS = {
    "surveyor": {"role": "writer", "password_hash": pwd.hash("surv123456")},
    "inspector": {"role": "reader", "password_hash": pwd.hash("insp123456")},
}

app = Flask(__name__)


def migrate():
    """create_all 之外的幂等小迁移：老库补列、补部分唯一索引。"""
    with engine.begin() as conn:
        if engine.dialect.name != "postgresql":
            # 本地 sqlite 验证：create_all 不给已存在的旧表补列，这里按 schema 补。
            cols = {r[1] for r in conn.execute(text("PRAGMA table_info(convergence_logs)"))}
            for col, ddl in (
                ("stake_id", "ALTER TABLE convergence_logs ADD COLUMN stake_id INTEGER"),
                ("section_no", "ALTER TABLE convergence_logs ADD COLUMN section_no VARCHAR"),
                ("coord_snapshot", "ALTER TABLE convergence_logs ADD COLUMN coord_snapshot FLOAT"),
            ):
                if col not in cols:
                    conn.execute(text(ddl))
            # 清掉可能建成全量的旧索引，换成同样语义的部分唯一索引。
            conn.execute(text("DROP INDEX IF EXISTS uq_open_log_per_stake"))
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_open_log_per_stake "
                    "ON convergence_logs (stake_id) WHERE status = 'pending'"
                )
            )
        else:
            for ddl in (
                "ALTER TABLE convergence_logs ADD COLUMN IF NOT EXISTS stake_id INTEGER",
                "ALTER TABLE convergence_logs ADD COLUMN IF NOT EXISTS section_no VARCHAR",
                "ALTER TABLE convergence_logs ADD COLUMN IF NOT EXISTS coord_snapshot FLOAT",
            ):
                conn.execute(text(ddl))
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_open_log_per_stake "
                    "ON convergence_logs (stake_id) WHERE status = 'pending'"
                )
            )
    # 老库回填：A01 遗留的单据没有册关联，按桩号补建已钉册记并挂上。
    db = SessionLocal()
    try:
        stakes_by_chainage = {
            c: (sid, sec) for c, sid, sec in db.query(
                Stake.chainage, Stake.id, Stake.section_no
            ).all()
        }
        legacy = db.query(ConvergenceLog).filter(ConvergenceLog.stake_id.is_(None)).all()
        for r in legacy:
            found = stakes_by_chainage.get(r.chainage)
            if found is None:
                section = r.section_no or f"S-{r.id:02d}"
                stake = Stake(
                    section_no=section,
                    chainage=r.chainage,
                    coordinate=0.0,
                    status="staked",
                    staked_by=r.created_by,
                    staked_at=r.created_at,
                    updated_at=r.created_at,
                )
                db.add(stake)
                db.flush()
                found = (stake.id, section)
                stakes_by_chainage[r.chainage] = found
            stake_id, section = found
            r.stake_id = stake_id
            if not r.section_no:
                r.section_no = section
            if r.coord_snapshot is None:
                r.coord_snapshot = 0.0
        db.commit()
    finally:
        db.close()


def seed():
    Base.metadata.create_all(engine)
    migrate()
    db = SessionLocal()
    try:
        if db.query(ConvergenceLog).count() > 0:
            return
        now = datetime.now(timezone.utc)
        for chainage, section_no, coordinate, delta, expect in (
            ("K12+180", "S-01", 3120.5, 1.2, "合格"),
            ("K18+040", "S-02", 3180.0, 5.6, "超限"),
        ):
            verdict, reason = judge(delta)
            assert verdict == expect
            stake = Stake(
                section_no=section_no,
                chainage=chainage,
                coordinate=coordinate,
                status="staked",
                staked_by="surveyor",
                staked_at=now,
                updated_at=now,
            )
            db.add(stake)
            db.flush()
            db.add(
                ConvergenceLog(
                    chainage=chainage,
                    delta_mm=delta,
                    status="done",
                    verdict=verdict,
                    reason=reason,
                    created_by="surveyor",
                    created_at=now,
                    processed_at=now,
                    stake_id=stake.id,
                    section_no=section_no,
                    coord_snapshot=coordinate,
                )
            )
        db.commit()
    finally:
        db.close()


seed()
if os.environ.get("CLAIMER_ENABLED", "1") == "1":
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
            return jsonify({"detail": "巡检员只读，不能钉桩也不能报送"}), 403
        g.user = user
        return fn(*args, **kwargs)

    return wrapper


def parse_coordinate(body):
    try:
        return float(body.get("coordinate")), None
    except (TypeError, ValueError):
        return None, "坐标必须是数字"


def parse_delta(body):
    try:
        return float(body.get("delta_mm")), None
    except (TypeError, ValueError):
        return None, "收敛值必须是数字"


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
        rows = db.query(Stake).order_by(Stake.id.desc()).all()
        return jsonify([stake_dict(r) for r in rows])
    finally:
        db.close()


@app.post("/api/stakes")
@require_writer
def create_stake():
    body = request.get_json(silent=True) or {}
    section_no = (body.get("section_no") or "").strip()
    chainage = (body.get("chainage") or "").strip()
    if not section_no:
        return jsonify({"detail": "断面号不能为空"}), 400
    if not chainage:
        return jsonify({"detail": "桩号不能为空"}), 400
    coordinate, err = parse_coordinate(body)
    if err:
        return jsonify({"detail": err}), 400
    db = SessionLocal()
    try:
        row = Stake(
            section_no=section_no,
            chainage=chainage,
            coordinate=coordinate,
            status="draft",
            updated_at=datetime.now(timezone.utc),
        )
        db.add(row)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            return jsonify({"detail": f"桩号 {chainage} 已在册，只能留下一条"}), 409
        db.refresh(row)
        return jsonify(stake_dict(row)), 201
    finally:
        db.close()


@app.post("/api/stakes/<int:stake_id>/drive")
@require_writer
def drive_stake(stake_id):
    """钉桩登记与报送同一次提交、同一事务：缺一边视为没做完，整体不落库。"""
    body = request.get_json(silent=True) or {}
    if body.get("delta_mm") is None:
        return jsonify(
            {"detail": "钉桩登记和报送必须同一次提交：缺收敛值视为没做完，请补齐后再钉桩"}
        ), 400
    delta_mm, err = parse_delta(body)
    if err:
        return jsonify({"detail": err}), 400

    db = SessionLocal()
    try:
        stake = db.query(Stake).filter(Stake.id == stake_id).with_for_update().first()
        if stake is None:
            return jsonify({"detail": "册中没有这条桩号"}), 404
        if stake.status == "staked":
            return jsonify({"detail": f"桩号 {stake.chainage} 已钉过，不能重复钉桩"}), 409

        now = datetime.now(timezone.utc)
        stake.status = "staked"
        stake.staked_by = g.user["username"]
        stake.staked_at = now
        stake.updated_at = now

        # 钉成功后坐标立刻抄进这一份单据（快照）。
        log = ConvergenceLog(
            chainage=stake.chainage,
            delta_mm=delta_mm,
            status="pending",
            created_by=g.user["username"],
            created_at=now,
            stake_id=stake.id,
            section_no=stake.section_no,
            coord_snapshot=stake.coordinate,
        )
        db.add(log)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            return jsonify({"detail": "该桩号被另一单抢先，本次钉桩报送失败"}), 409
        db.refresh(stake)
        db.refresh(log)
        return jsonify({"stake": stake_dict(stake), "log": row_dict(log)}), 201
    finally:
        db.close()


@app.patch("/api/stakes/<int:stake_id>")
@require_writer
def update_stake(stake_id):
    """专页改坐标：只动坐标册，绝不回写任何已办结单据的快照。"""
    body = request.get_json(silent=True) or {}
    coordinate, err = parse_coordinate(body)
    if err:
        return jsonify({"detail": err}), 400
    db = SessionLocal()
    try:
        stake = db.query(Stake).filter(Stake.id == stake_id).with_for_update().first()
        if stake is None:
            return jsonify({"detail": "册中没有这条桩号"}), 404
        stake.coordinate = coordinate
        stake.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(stake)
        return jsonify(stake_dict(stake))
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


@app.post("/api/logs")
@require_writer
def create_log():
    body = request.get_json(silent=True) or {}
    delta_mm, err = parse_delta(body)
    if err:
        return jsonify({"detail": err}), 400
    stake_id = body.get("stake_id")
    if not isinstance(stake_id, int):
        return jsonify({"detail": "报送必须挂到坐标册里的桩号"}), 400

    db = SessionLocal()
    try:
        stake = db.query(Stake).filter(Stake.id == stake_id).with_for_update().first()
        if stake is None:
            return jsonify({"detail": "册中没有这条桩号，先在钉桩专页登记"}), 400

        now = datetime.now(timezone.utc)
        if stake.status != "staked":
            # 没钉桩就报送：整份退回，退单原因留痕。
            rejected = ConvergenceLog(
                chainage=stake.chainage,
                delta_mm=delta_mm,
                status="rejected",
                verdict=None,
                reason="还没钉桩：报送前必须先在钉桩专页钉桩，单据整份退回",
                created_by=g.user["username"],
                created_at=now,
                stake_id=stake.id,
                section_no=stake.section_no,
                coord_snapshot=None,
            )
            db.add(rejected)
            db.commit()
            db.refresh(rejected)
            return jsonify({"detail": rejected.reason, "log": row_dict(rejected)}), 422

        row = ConvergenceLog(
            chainage=stake.chainage,
            delta_mm=delta_mm,
            status="pending",
            created_by=g.user["username"],
            created_at=now,
            stake_id=stake.id,
            section_no=stake.section_no,
            coord_snapshot=stake.coordinate,
        )
        db.add(row)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            return jsonify({"detail": "该桩号已有在办单，两人抢单只留一单，本次报送失败"}), 409
        db.refresh(row)
        return jsonify(row_dict(row)), 201
    finally:
        db.close()
