"""Benchmark fixtures must obey the same public import contract as real inputs."""

import io
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from benchmark_acceptance import payloads, variability

from routeops.application.import_validation import ImportLimits, validate_package


@pytest.mark.parametrize(
    "model,profile,kind",
    [("B2B", "small", "csv"), ("B2C", "medium", "xlsx"), ("B2B", "bounded", "xlsx")],
)
def test_seeded_benchmark_inputs_are_valid_and_reproducible(tmp_path, model, profile, kind):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    files, rows = payloads(first, model, profile, kind, 41042)
    again, _ = payloads(second, model, profile, kind, 41042)
    assert files == again
    if kind == "xlsx":
        with zipfile.ZipFile(io.BytesIO(files["package.xlsx"])) as archive:
            assert all(entry.date_time == (1980, 1, 1, 0, 0, 0) for entry in archive.infolist())
    result = validate_package(files, ImportLimits())
    assert result.valid, result.to_dict()
    assert {name: count.total for name, count in result.counts.items()} == {
        name: len(values) for name, values in rows.items()
    }


def test_variability_keeps_samples_and_singleton_uncertainty():
    result = variability(
        [
            {"http_seconds": {"upload": 1, "publication": 3}},
            {"http_seconds": {"upload": 2}},
            {"http_seconds": {"upload": 6}},
        ]
    )
    assert result["upload"]["median"] == 2
    assert result["upload"]["min"] == 1 and result["upload"]["max"] == 6
    assert result["upload"]["stdev"] > 0
    assert result["publication"]["stdev"] is None
