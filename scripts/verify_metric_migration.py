#!/usr/bin/env python3
"""
Verify the metric store v1→v2 migration works end-to-end through HA's real
``Store.async_load`` path (the path that crashed with NotImplementedError).

This is a one-off verification script for the fix in store.py.  It:
  1. Restores the backed-up v1 metrics file (daily-discard shape).
  2. Builds the real ``AxDoseLoggerStore`` against a minimal real ``HomeAssistant``
     and runs ``async_load`` — which must NOT raise and must migrate v1→v2.
  3. Asserts the on-disk file is now version 2 with the v1 data preserved in
     the date-keyed shape, and that re-loading the migrated file is idempotent
     (no migration re-run, no re-save, data unchanged).
  4. Restores the original v1 backup so the live HA instance does the real
     migration on its next start.

Run:  python3 scripts/verify_metric_migration.py
"""

import asyncio
import json
import shutil
import sys
from pathlib import Path

# Make the custom_components package importable using the real HA install.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from homeassistant.core import HomeAssistant

from custom_components.ax_dose_logger.store import (
    METRIC_STORAGE_VERSION,
    METRIC_STORE_KEY,
    MetricStore,
    _migrate_metric_v1_to_v2,
)

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
STORAGE_DIR = CONFIG_DIR / ".storage"
METRIC_PATH = STORAGE_DIR / METRIC_STORE_KEY
BACKUP_PATH = STORAGE_DIR / "ax_dose_logger_metrics.v1.bak"


def _read_json(path: Path) -> dict:
    with path.open() as f:
        return json.load(f)


def _restore_v1() -> None:
    """Copy the v1 backup over the live metrics file so we test migration cold."""
    shutil.copy(BACKUP_PATH, METRIC_PATH)


async def _new_hass() -> HomeAssistant:
    """Build a minimal real HomeAssistant with a config path + event loop."""
    # storage.py needs hass.config.path(STORAGE_DIR, key) → <config>/.storage/<key>
    # HomeAssistant(__init__) sets hass.config.config_dir = CONFIG_DIR, so
    # hass.config.path("foo") returns <CONFIG_DIR>/foo.  Good.
    return HomeAssistant(str(CONFIG_DIR))


async def main() -> None:
    if not BACKUP_PATH.exists():
        print(f"FAIL: backup {BACKUP_PATH} not found")
        sys.exit(1)

    # Sanity: the unit-level migration helper produces the expected v2 shape
    # from the raw v1 data dict (independent of HA Store plumbing).
    v1_data = _read_json(BACKUP_PATH)["data"]
    migrated = _migrate_metric_v1_to_v2(v1_data)
    entry = migrated["01KVNT487A40NQGM45787HGZXM"]
    assert entry["nausea"] == {"2026-07-06": 2.0}, entry["nausea"]
    assert entry["pain"] == {"2026-07-06": 4.0}
    assert entry["mood"] == {"2026-07-06": 4.0}
    assert entry["fatigue"] == {"2026-07-06": 4.0}
    print("STEP 1: _migrate_metric_v1_to_v2 helper produces correct v2 shape — OK")

    # STEP 2: cold migration through real HA Store (the path that crashed).
    _restore_v1()
    on_disk_before = _read_json(METRIC_PATH)
    assert on_disk_before["version"] == 1, on_disk_before["version"]
    hass = await _new_hass()
    store = MetricStore(hass, METRIC_STORAGE_VERSION, METRIC_STORE_KEY)
    loaded = await store.async_load()
    assert isinstance(loaded, dict), type(loaded)
    on_disk_after = _read_json(METRIC_PATH)
    assert on_disk_after["version"] == 2, on_disk_after["version"]
    migrated_entry = on_disk_after["data"]["01KVNT487A40NQGM45787HGZXM"]
    assert migrated_entry["nausea"] == {"2026-07-06": 2.0}
    assert migrated_entry["pain"] == {"2026-07-06": 4.0}
    print("STEP 2: cold v1→v2 migration through HA Store — OK (on-disk now v2)")
    print("        loaded shape:", json.dumps(loaded["01KVNT487A40NQGM45787HGZXM"]))

    # STEP 3: idempotency — re-loading the now-v2 file must NOT migrate again
    # and must NOT raise.  (HA only calls _async_migrate_func when versions differ.)
    loaded2 = await store.async_load()
    assert isinstance(loaded2, dict)
    assert loaded2 == loaded
    on_disk_after2 = _read_json(METRIC_PATH)
    assert on_disk_after2["version"] == 2
    assert on_disk_after2["data"] == on_disk_after["data"]
    print("STEP 3: re-load of migrated v2 file is idempotent — OK")

    # STEP 4: unknown future version must surface NotImplementedError, not
    # silently corrupt.  (Matches HA core registry pattern.)
    # (Static check — we don't write a v3 file to disk; just call the override.)
    fut_unknown = MetricStore._async_migrate_func(store, 99, 1, {})
    try:
        await fut_unknown
        print("STEP 4: FAIL — expected NotImplementedError for unknown version")
        sys.exit(1)
    except NotImplementedError:
        print("STEP 4: unknown major version raises NotImplementedError — OK")

    # Restore the original v1 file so the live HA instance performs the real
    # migration on its next boot (this is the production path).
    _restore_v1()
    print("\nALL STEPS PASSED — v1 metrics file restored for live HA migration")


if __name__ == "__main__":
    asyncio.run(main())
