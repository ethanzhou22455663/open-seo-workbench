"""Entry-point contract for optional API providers.

Provider packages are separate installations. They never change core tables or
the rule engine; they yield normalized records to the same importer as files.
"""

from dataclasses import dataclass
from importlib.metadata import entry_points
from typing import Iterable, Protocol

from .imports import import_records, normalize_geo
from .services import site_settings


@dataclass(frozen=True)
class AdapterBatch:
    dataset: str
    rows: list[dict]
    grain: str = "month"
    geo: str = "GLOBAL"
    default_period: str | None = None
    coverage: str = "complete"


class DataSourceAdapter(Protocol):
    name: str
    provider: str

    def fetch(self, *, site: dict, settings: dict, start: str, end: str,
              geo: str) -> Iterable[AdapterBatch]: ...


def installed_adapters():
    return {entry.name: entry for entry in entry_points(group="open_seo_workbench.adapters")}


def adapter_names():
    return sorted(installed_adapters())


def sync_adapter(site_id, adapter_name, start, end, geo="GLOBAL"):
    from .storage import site_info
    selected = installed_adapters().get(adapter_name)
    if selected is None:
        raise ValueError(f"Adapter {adapter_name!r} is not installed")
    adapter: DataSourceAdapter = selected.load()()
    geo = normalize_geo(geo)
    results = []
    for batch in adapter.fetch(site=site_info(site_id), settings=site_settings(site_id),
                               start=start, end=end, geo=geo):
        results.append(import_records(site_id, batch.rows, dataset=batch.dataset,
                                      source_name=adapter.name, provider=adapter.provider,
                                      geo=batch.geo, grain=batch.grain,
                                      default_period=batch.default_period,
                                      coverage=batch.coverage, actor="adapter"))
    return {"adapter": adapter_name, "site_id": site_id, "runs": results}
