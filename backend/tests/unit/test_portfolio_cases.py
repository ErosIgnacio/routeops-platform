"""Portfolio contracts stay independent of historical demos and bounded workloads."""

import pytest

from routeops.application.import_validation import validate_package
from routeops.infrastructure.data.operation_cases import case_rows
from routeops.infrastructure.data.portfolio_cases import payloads, portfolio_rows


@pytest.mark.parametrize("model", ["b2b", "b2c"])
@pytest.mark.parametrize("workbook", [False, True])
def test_portfolio_csv_xlsx_are_valid_and_preserve_the_original_cases(model, workbook):
    original = case_rows(f"{model}-feasible")
    files = payloads(model, workbook)
    report = validate_package(files)
    assert report.valid, report.to_dict()
    rows = portfolio_rows(model)
    assert len(rows["vehicles"]) == 2
    assert len(rows["orders"]) == (4 if model == "b2b" else 6)
    assert len({(r["latitude"], r["longitude"]) for r in rows["orders"]}) == len(rows["orders"])
    assert case_rows(f"{model}-feasible") == original
    assert all(int(r["externally_reserved_quantity"]) == 5 for r in rows["inventory"])
