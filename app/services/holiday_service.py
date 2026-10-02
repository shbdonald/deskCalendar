# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from PySide6.QtCore import QObject, QThread, Signal

from .config_store import app_data_dir
from .country_names import SEED_COUNTRIES, build_country_choices

COUNTRIES_CACHE_MAX_AGE_DAYS = 30


class HolidayFetchWorker(QThread):
    finished_ok = Signal(str, int, list)
    finished_err = Signal(str, int, str)

    def __init__(self, country: str, year: int, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.country = country
        self.year = year

    def run(self) -> None:
        url = f"https://date.nager.at/api/v3/PublicHolidays/{self.year}/{self.country}"
        try:
            with httpx.Client(timeout=20.0) as client:
                resp = client.get(url)
                resp.raise_for_status()
                data = resp.json()
                if not isinstance(data, list):
                    raise ValueError("unexpected response")
                self.finished_ok.emit(self.country, self.year, data)
        except Exception as exc:  # noqa: BLE001
            self.finished_err.emit(self.country, self.year, str(exc))


class AvailableCountriesWorker(QThread):
    finished_ok = Signal(list)
    finished_err = Signal(str)

    def run(self) -> None:
        try:
            with httpx.Client(timeout=15.0) as client:
                resp = client.get("https://date.nager.at/api/v3/AvailableCountries")
                resp.raise_for_status()
                data = resp.json()
                result: list[tuple[str, str]] = []
                for item in data:
                    code = str(item.get("countryCode", "")).upper()
                    name = str(item.get("name", code))
                    if code:
                        result.append((code, name))
                self.finished_ok.emit(result)
        except Exception as exc:  # noqa: BLE001
            self.finished_err.emit(str(exc))


class HolidayService(QObject):
    """Loads and caches public holidays for selected countries."""

    updated = Signal()
    countries_refreshed = Signal(object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._cache: dict[tuple[str, int], list[dict[str, Any]]] = {}
        self._by_date: dict[str, list[dict[str, str]]] = {}
        self._countries: list[str] = []
        self._workers: list[QThread] = []
        self._pending_fetch: set[tuple[str, int]] = set()
        self._raw_countries: list[tuple[str, str]] = list(SEED_COUNTRIES)
        self._available: list[tuple[str, str]] = build_country_choices(self._raw_countries)
        self._load_countries_cache()
        # Monthly refresh when cache missing or older than 30 days.
        self.refresh_available_from_api_async()

    def set_countries(self, countries: list[str]) -> None:
        self._countries = [c.upper() for c in countries if c]
        self._rebuild_index()
        self.ensure_years([date.today().year])

    def countries(self) -> list[str]:
        return list(self._countries)

    def holidays_for(self, d: date) -> list[dict[str, str]]:
        return list(self._by_date.get(d.isoformat(), []))

    def cache_path(self, country: str, year: int) -> Path:
        return app_data_dir() / "holidays" / f"{country.upper()}_{year}.json"

    def countries_cache_path(self) -> Path:
        return app_data_dir() / "countries.json"

    def _load_file(self, country: str, year: int) -> list[dict[str, Any]] | None:
        path = self.cache_path(country, year)
        if not path.exists():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return raw if isinstance(raw, list) else None
        except (json.JSONDecodeError, OSError):
            return None

    def _save_file(self, country: str, year: int, data: list[dict[str, Any]]) -> None:
        path = self.cache_path(country, year)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _parse_countries_payload(self, raw: Any) -> list[tuple[str, str]] | None:
        if not isinstance(raw, dict):
            return None
        items = raw.get("countries")
        if not isinstance(items, list):
            return None
        pairs: list[tuple[str, str]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            code = str(item.get("code", "")).upper()
            name = str(item.get("name", code))
            if code:
                pairs.append((code, name))
        return pairs or None

    def _countries_cache_age_days(self, raw: dict[str, Any]) -> float | None:
        stamp = raw.get("fetched_at")
        if not isinstance(stamp, str) or not stamp:
            return None
        try:
            fetched = datetime.fromisoformat(stamp)
            if fetched.tzinfo is None:
                fetched = fetched.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            return (now - fetched.astimezone(timezone.utc)).total_seconds() / 86400.0
        except ValueError:
            return None

    def _load_countries_cache(self) -> bool:
        path = self.countries_cache_path()
        if not path.exists():
            return False
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return False
        pairs = self._parse_countries_payload(raw)
        if not pairs:
            return False
        self._raw_countries = pairs
        self._available = build_country_choices(pairs)
        return True

    def _save_countries_cache(self, pairs: list[tuple[str, str]]) -> None:
        path = self.countries_cache_path()
        payload = {
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "source": "https://date.nager.at/api/v3/AvailableCountries",
            "countries": [{"code": c, "name": n} for c, n in pairs],
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _countries_cache_is_fresh(self) -> bool:
        path = self.countries_cache_path()
        if not path.exists():
            return False
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return False
        if not isinstance(raw, dict) or self._parse_countries_payload(raw) is None:
            return False
        age = self._countries_cache_age_days(raw)
        return age is not None and age < COUNTRIES_CACHE_MAX_AGE_DAYS

    def ensure_years(self, years: list[int]) -> None:
        for year in years:
            for country in self._countries:
                key = (country, year)
                if key in self._cache or key in self._pending_fetch:
                    continue
                cached = self._load_file(country, year)
                if cached is not None:
                    self._cache[key] = cached
                    continue
                self._fetch_async(country, year)
        self._rebuild_index()
        self.updated.emit()

    def _fetch_async(self, country: str, year: int) -> None:
        key = (country, year)
        self._pending_fetch.add(key)
        worker = HolidayFetchWorker(country, year, self)
        worker.finished_ok.connect(self._on_fetched)
        worker.finished_err.connect(self._on_fetch_err)
        worker.finished.connect(lambda w=worker: self._workers.remove(w) if w in self._workers else None)
        self._workers.append(worker)
        worker.start()

    def _on_fetched(self, country: str, year: int, data: list) -> None:
        key = (country, year)
        self._pending_fetch.discard(key)
        self._cache[key] = data
        self._save_file(country, year, data)
        self._rebuild_index()
        self.updated.emit()

    def _on_fetch_err(self, country: str, year: int, _msg: str) -> None:
        # Do not cache failures — allow retry on next ensure_years.
        self._pending_fetch.discard((country, year))

    def _rebuild_index(self) -> None:
        index: dict[str, list[dict[str, str]]] = {}
        for country in self._countries:
            for (cc, _year), items in self._cache.items():
                if cc != country:
                    continue
                for item in items:
                    day = item.get("date")
                    name = item.get("localName") or item.get("name") or "Holiday"
                    if not day:
                        continue
                    index.setdefault(day, []).append({"country": country, "name": str(name)})
        self._by_date = index

    def available_countries(self) -> list[tuple[str, str]]:
        return list(self._available)

    def refresh_available_from_api_async(self, *, force: bool = False) -> None:
        """Refresh country list from network when cache missing/stale (≈ monthly)."""
        if not force and self._countries_cache_is_fresh():
            return
        worker = AvailableCountriesWorker(self)
        worker.finished_ok.connect(self._on_countries_ok)
        worker.finished_err.connect(lambda _m: None)
        worker.finished.connect(lambda w=worker: self._workers.remove(w) if w in self._workers else None)
        self._workers.append(worker)
        worker.start()

    def shutdown(self) -> None:
        """Stop background fetch threads so the process can exit cleanly."""
        for worker in list(self._workers):
            worker.requestInterruption()
            worker.quit()
            if not worker.wait(400):
                worker.terminate()
                worker.wait(200)
        self._workers.clear()
        self._pending_fetch.clear()

    def _on_countries_ok(self, result: list) -> None:
        normalized: list[tuple[str, str]] = []
        for item in result:
            if isinstance(item, (tuple, list)) and len(item) >= 2:
                code = str(item[0]).upper()
                name = str(item[1])
                if code:
                    normalized.append((code, name))
        if not normalized:
            return
        self._raw_countries = normalized
        self._available = build_country_choices(normalized)
        self._save_countries_cache(normalized)
        self.countries_refreshed.emit(list(self._available))
