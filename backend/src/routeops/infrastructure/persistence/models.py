from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from geoalchemy2 import Geometry
from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class PlanningRunModel(Base):
    __tablename__ = "planning_runs"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    scenario_name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    input_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    result_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    kpis: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)

    routes: Mapped[list[OptimizedRouteModel]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    unassigned: Mapped[list[UnassignedOrderModel]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class OptimizedRouteModel(Base):
    __tablename__ = "optimized_routes"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("planning_runs.id", ondelete="CASCADE"), nullable=False
    )
    vehicle_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_vehicle_id: Mapped[str] = mapped_column(String(100), nullable=False)
    distribution_center_id: Mapped[str] = mapped_column(String(100), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    distance_meters: Mapped[int] = mapped_column(Integer, nullable=False)
    total_duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    geometry: Mapped[Any | None] = mapped_column(Geometry("LINESTRING", srid=4326))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    run: Mapped[PlanningRunModel] = relationship(back_populates="routes")

    __table_args__ = (Index("ix_optimized_routes_run_sequence", "run_id", "sequence"),)


class UnassignedOrderModel(Base):
    __tablename__ = "unassigned_orders"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("planning_runs.id", ondelete="CASCADE"), nullable=False
    )
    order_id: Mapped[str] = mapped_column(String(100), nullable=False)
    stage: Mapped[str] = mapped_column(String(40), nullable=False)
    reasons: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)

    run: Mapped[PlanningRunModel] = relationship(back_populates="unassigned")

    __table_args__ = (Index("ix_unassigned_orders_run", "run_id"),)
