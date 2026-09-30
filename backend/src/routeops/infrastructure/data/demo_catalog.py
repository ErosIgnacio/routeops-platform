"""Prepare isolated published scenarios from the four allocation fixtures."""

from __future__ import annotations

import csv
import hashlib
import io
from datetime import date, datetime
from typing import Any
from uuid import UUID

from routeops.application.import_context import ValidationContext
from routeops.application.import_contract import DATASETS, SCHEMA
from routeops.application.import_upload import ReceivedFile, ReceivedPackage
from routeops.infrastructure.data.allocation_demos import AllocationDemo, allocation_demos
from routeops.infrastructure.persistence.import_publication import ImportPublicationService
from routeops.infrastructure.persistence.import_upload_repository import UploadService
from routeops.infrastructure.persistence.import_validation_jobs import ValidationJobService
from routeops.infrastructure.persistence.planning_data_repository import ScenarioRepository
from routeops.infrastructure.storage.local import LocalObjectStorage

CATALOG = (
    ("original", "Demo original · ORD-003"),
    ("exclusive_stock", "Stock exclusivo por CD"),
    ("choice_between_centers", "Elección entre CDs"),
    ("shared_stock_restricted", "Stock compartido y pedido restringido"),
    ("fleet_restrictions", "Restricciones de flota"),
)


def demo_rows(demo: AllocationDemo) -> dict[str, list[dict[str, str]]]:
    rows: dict[str, list[dict[str, str]]] = {name: [] for name in DATASETS}
    for center in demo.centers:
        rows["distribution_centers"].append(
            {
                "distribution_center_id": center.id,
                "name": center.name,
                "latitude": str(center.location.latitude),
                "longitude": str(center.location.longitude),
                "operating_start": "08:00",
                "operating_end": "18:00",
            }
        )
    for order in demo.orders:
        rows["orders"].append(
            {
                "order_id": order.id,
                "customer_reference": order.customer_reference,
                "latitude": str(order.location.latitude),
                "longitude": str(order.location.longitude),
                "priority": str(order.priority),
                "time_window_start": order.time_window_start.isoformat(),
                "time_window_end": order.time_window_end.isoformat(),
                "service_minutes": str(order.service_seconds // 60),
                "required_skills": "|".join(sorted(order.required_skills)),
            }
        )
        for line in order.lines:
            rows["order_lines"].append(
                {
                    "order_id": order.id,
                    "sku": line.sku,
                    "quantity": str(line.quantity),
                    "unit_weight_kg": str(line.unit_weight_kg),
                    "unit_volume_m3": str(line.unit_volume_m3),
                }
            )
    for stock in demo.stock:
        rows["inventory"].append(
            {
                "snapshot_at": "2026-10-15T08:00:00-03:00",
                "distribution_center_id": stock.center_id,
                "sku": stock.sku,
                "on_hand_quantity": str(stock.on_hand),
                "externally_reserved_quantity": str(stock.externally_reserved),
                "safety_stock_quantity": str(stock.safety_stock),
            }
        )
    for vehicle in demo.vehicles:
        rows["vehicles"].append(
            {
                "vehicle_id": vehicle.id,
                "distribution_center_id": vehicle.distribution_center_id,
                "vehicle_type": "van",
                "capacity_units": str(vehicle.capacity_units),
                "capacity_weight_kg": str(vehicle.capacity_weight_kg),
                "capacity_volume_m3": str(vehicle.capacity_volume_m3),
                "shift_start": "08:00",
                "shift_end": "18:00",
                "skills": "|".join(sorted(vehicle.skills)),
                "fixed_cost": "100",
                "cost_per_hour": "10",
                "cost_per_km": "1",
            }
        )
    return rows


def _package(storage: LocalObjectStorage, rows: dict[str, list[dict[str, str]]]) -> ReceivedPackage:
    files: list[ReceivedFile] = []
    for name in DATASETS:
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=[spec.name for spec in SCHEMA[name]])
        writer.writeheader()
        writer.writerows(rows[name])
        handle = storage.begin()
        handle.write(stream.getvalue().encode("utf-8"))
        files.append(ReceivedFile(name, f"{name}.csv", handle.finish()))
    manifest = hashlib.sha256()
    for item in sorted(files, key=lambda file: file.dataset):
        manifest.update(
            f"{item.dataset}\0{item.object.sha256}\0{item.object.size_bytes}\n".encode("ascii")
        )
    return ReceivedPackage(tuple(files), manifest.hexdigest())


class DemoCatalogService:
    def __init__(
        self,
        scenarios: ScenarioRepository,
        storage: LocalObjectStorage,
        uploads: UploadService,
        validation: ValidationJobService,
        publication: ImportPublicationService,
    ) -> None:
        self.scenarios = scenarios
        self.storage = storage
        self.uploads = uploads
        self.validation = validation
        self.publication = publication

    @staticmethod
    def list() -> list[dict[str, str]]:
        return [{"id": name, "title": title} for name, title in CATALOG]

    def prepare(self, name: str) -> dict[str, Any]:
        demos = allocation_demos()
        if name not in demos:
            raise ValueError("DEMO_NOT_FOUND")
        scenario_id: UUID = self.scenarios.create(f"Demo 2.4 · {name}")
        package = _package(self.storage, demo_rows(demos[name]))
        batch_id, _ = self.uploads.create(scenario_id, "demo-fixture", package)
        context = ValidationContext(
            planning_date=date(2026, 10, 15),
            horizon_start_at=datetime.fromisoformat("2026-10-15T08:00:00-03:00"),
            horizon_end_at=datetime.fromisoformat("2026-10-15T18:00:00-03:00"),
            timezone_iana="America/Santiago",
            currency="CLP",
        )
        self.validation.request(scenario_id, batch_id, context)
        for _ in range(20):
            status = self.validation.get(scenario_id, batch_id)["status"]
            if status in ("VALID", "INVALID", "FAILED"):
                break
            self.validation.process_once()
        final = self.validation.get(scenario_id, batch_id)
        if final["status"] != "VALID":
            raise RuntimeError("demo fixture did not pass import validation")
        revision, _ = self.publication.publish(scenario_id, batch_id)
        return {"demo": name, "scenario_id": str(scenario_id), "revision": revision}
