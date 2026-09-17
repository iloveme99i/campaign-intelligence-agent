"""Export reproducible synthetic CSVs; never reads the local application DB."""

from pathlib import Path

from analytics_agent.merchant.examples import example_exports
from analytics_agent.merchant.importer import parse_exports

destination = Path(__file__).resolve().parents[1] / "sample_data"
destination.mkdir(exist_ok=True)
exports = example_exports()
parse_exports(exports)
for name, contents in exports.items():
    (destination / f"{name}.csv").write_bytes(contents)
print("Validated synthetic CSV files exported to sample_data/")
