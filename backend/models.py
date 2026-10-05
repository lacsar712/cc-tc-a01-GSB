import os
from datetime import datetime, timezone

from sqlalchemy import (
    DateTime,
    Float,
    Index,
    Integer,
    String,
    create_engine,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

DSN = os.environ.get("DATABASE_URL", "postgresql://app:app@localhost:54401/tunnelconv")
_connect_args = (
    {"check_same_thread": False, "timeout": 10} if DSN.startswith("sqlite") else {}
)
engine = create_engine(DSN, pool_pre_ping=True, connect_args=_connect_args)
SessionLocal = sessionmaker(bind=engine)


class Base(DeclarativeBase):
    pass


class Stake(Base):
    """断面坐标册：一个桩号一条册记，钉桩后坐标即登记在册。"""

    __tablename__ = "stakes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    section_no: Mapped[str] = mapped_column(String, nullable=False)
    chainage: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    coordinate: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="draft")
    staked_by: Mapped[str | None] = mapped_column(String, nullable=True)
    staked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)


class ConvergenceLog(Base):
    __tablename__ = "convergence_logs"
    __table_args__ = (
        # 同一桩号同时只允许一张在办单（pending）；办结/退单的不占坑。
        Index(
            "uq_open_log_per_stake",
            "stake_id",
            unique=True,
            sqlite_where=text("status = 'pending'"),
            postgresql_where=text("status = 'pending'"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chainage: Mapped[str] = mapped_column(String, nullable=False)
    delta_mm: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    verdict: Mapped[str | None] = mapped_column(String, nullable=True)
    reason: Mapped[str | None] = mapped_column(String, nullable=True)
    created_by: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # 钉桩关联：stake_id 指向坐标册；coord_snapshot 是钉桩瞬间抄进单据的坐标。
    stake_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    section_no: Mapped[str | None] = mapped_column(String, nullable=True)
    coord_snapshot: Mapped[float | None] = mapped_column(Float, nullable=True)


def stake_dict(row: Stake) -> dict:
    return {
        "id": row.id,
        "section_no": row.section_no,
        "chainage": row.chainage,
        "coordinate": row.coordinate,
        "status": row.status,
        "staked_by": row.staked_by,
        "staked_at": row.staked_at.isoformat() if row.staked_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def row_dict(row: ConvergenceLog) -> dict:
    return {
        "id": row.id,
        "chainage": row.chainage,
        "delta_mm": row.delta_mm,
        "status": row.status,
        "verdict": row.verdict,
        "reason": row.reason,
        "created_by": row.created_by,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "processed_at": row.processed_at.isoformat() if row.processed_at else None,
        "stake_id": row.stake_id,
        "section_no": row.section_no,
        "coord_snapshot": row.coord_snapshot,
    }
