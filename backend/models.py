import os
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    Integer,
    String,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

DSN = os.environ.get("DATABASE_URL", "postgresql://app:app@localhost:54401/tunnelconv")
engine = create_engine(DSN, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine)


class Base(DeclarativeBase):
    pass


class SectionStake(Base):
    """断面坐标册：一个桩号只能被钉进一次（钉桩档案）。"""

    __tablename__ = "section_stakes"
    __table_args__ = (UniqueConstraint("chainage", name="uq_stake_chainage"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chainage: Mapped[str] = mapped_column(String, nullable=False)
    coordinate: Mapped[str] = mapped_column(String, nullable=False)
    staked_by: Mapped[str] = mapped_column(String, nullable=False)
    staked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ConvergenceLog(Base):
    """测缝单据：status 为 pending（待认领）/ done（已办结）/ returned（退回）。

    coordinate_snapshot 是钉桩成功时抄进本单据的坐标快照，此后册上改坐标
    不再回写任何单据，保证已办结结论可复现。
    """

    __tablename__ = "convergence_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chainage: Mapped[str] = mapped_column(String, nullable=False)
    delta_mm: Mapped[float | None] = mapped_column(Float, nullable=True)
    coordinate_snapshot: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    verdict: Mapped[str | None] = mapped_column(String, nullable=True)
    reason: Mapped[str | None] = mapped_column(String, nullable=True)
    created_by: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


def stake_dict(row: SectionStake) -> dict:
    return {
        "id": row.id,
        "chainage": row.chainage,
        "coordinate": row.coordinate,
        "staked_by": row.staked_by,
        "staked_at": row.staked_at.isoformat() if row.staked_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def row_dict(row: ConvergenceLog) -> dict:
    return {
        "id": row.id,
        "chainage": row.chainage,
        "delta_mm": row.delta_mm,
        "coordinate_snapshot": row.coordinate_snapshot,
        "status": row.status,
        "verdict": row.verdict,
        "reason": row.reason,
        "created_by": row.created_by,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "processed_at": row.processed_at.isoformat() if row.processed_at else None,
    }
