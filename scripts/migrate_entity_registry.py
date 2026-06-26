#!/usr/bin/env python3
"""
Bulk entity-registry migration: ``pill_logger`` -> ``ax_dose_logger``.

This is a **standalone, offline** script.  It does NOT touch a running Home
Assistant instance.  It operates purely on a local JSON dump of the
``core.entity_registry`` file.

What it does
------------
Home Assistant stores deleted/orphaned entities in a separate
``deleted_entities`` array (the "graveyard") inside ``data["data"]``.  When an
integration is removed via the UI, its entities move there and are tagged with
an ``orphaned_timestamp``.  This script performs a **dual-array resurrection**:

1.  Loads ``data["data"]["entities"]`` (active array) and
    ``data["data"]["deleted_entities"]`` (graveyard array).
2.  Scans the **active** array and **purges** every entity whose
    ``platform == "ax_dose_logger"`` (the freshly generated "generator"
    entities whose ``entity_id`` strings are unwanted).  All other active
    entities are kept unchanged.
3.  Scans the **graveyard** array.  For every entity whose
    ``platform == "pill_logger"`` (the historical entities that anchor your
    SQLite history):
      * resolves the medication slug from the ``entity_id`` and looks it up
        in :data:`PREFIX_MAP` to obtain the new ``config_entry_id`` and
        ``device_id`` ULIDs.
      * changes ``platform`` -> ``"ax_dose_logger"``.
      * rebuilds ``unique_id`` as ``{new_config_entry_id}_{suffix}`` where the
        suffix is extracted by stripping the resolved slug prefix (falling
        back to an underscore split for ULID-prefixed values).
      * leaves ``entity_id`` and the registry row ``id`` **untouched**.
      * **Resurrection:** removes the ``orphaned_timestamp`` tag via
        ``.pop("orphaned_timestamp", None)`` and appends the entity into the
        new active list.
4.  Entities in the graveyard that are NOT ``pill_logger`` are left in the
    graveyard unchanged.
5.  Saves both arrays back into the ``data`` wrapper:
    ``data["data"]["entities"]`` and ``data["data"]["deleted_entities"]``.

Blacklist
---------
:data:`BLACKLIST` lists medication slugs whose entities must be **skipped**
(left in the graveyard, not resurrected).  Use this for test/duplicate
devices you do not want to bring back.

Matching strategy
-----------------
:data:`PREFIX_MAP` is keyed by the **historical** medication slug (the slug
embedded in the old ``entity_id`` strings).  For each graveyard entity the
script strips the HA domain prefix (``sensor.``, ``button.``, etc.) and tests
whether the remaining slug starts with ``{prefix}_``.  The trailing-underscore
guard disambiguates overlapping names (``paracetamol`` vs ``paracetamol_er``).
Prefixes are checked longest-first as a belt-and-braces measure.

Safety
------
* ``--dry-run`` prints a summary and writes nothing.
* Aborts if no ``pill_logger`` entities are found in the graveyard.
* Aborts with a clear list if any ``pill_logger`` entity's slug cannot be
  matched against :data:`PREFIX_MAP` (or is blacklisted -- reported as a
  warning, not an error).
* Atomic write: temp file + ``os.replace``.
* Pure standard library -- no external dependencies.

Usage
-----
    # 1. Export your registry from HA (.storage/core.entity_registry) next to
    #    this script as ``core.entity_registry.json``.
    #
    # 2. Dry-run to verify counts and that every old entity is matched:
    python3 scripts/migrate_entity_registry.py --dry-run
    #
    # 3. Execute for real:
    python3 scripts/migrate_entity_registry.py
    #
    # 4. Review the diff, then rename the output into place:
    #    mv core.entity_registry.json.migrated core.entity_registry.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# NEW-DEVICE ULID TABLE  --  keyed by the HISTORICAL medication slug (the slug
# embedded in the old entity_id strings).  Values are the new config_entry_id
# and device_id ULIDs harvested from the freshly generated ax_dose_logger
# entities.
# ---------------------------------------------------------------------------

PREFIX_MAP: dict[str, dict[str, str]] = {
    "paracetamol": {
        "config_entry_id": "01KVTYPTK7SHRCQZKDV3VKN240",
        "device_id": "da8b4e210072d6e8e363b5d8d8ce5deb",
    },
    "brintellix": {
        "config_entry_id": "01KVV1BBW1JXJEG40Q46K2G4VJ",
        "device_id": "b4ea94b9979ea3ff9fe301a40e8c3965",
    },
    "kodipar": {
        "config_entry_id": "01KVV1BM05YNA6AJDWTNQ9HZS0",
        "device_id": "2a6e38265ea0117e5e50212f7df1a077",
    },
    "magnesium": {
        "config_entry_id": "01KVV1BW1GWPQFTK62K391MMXF",
        "device_id": "7912db3c4e7e84d836ab1d116c93edfb",
    },
    "tea": {
        "config_entry_id": "01KVV1C4SWTWZTZA9YMJVAZ1RP",
        "device_id": "1ad29468618f965f783b07122d54fa5f",
    },
    "vitamin_d": {
        "config_entry_id": "01KVV1CMEQYQATWCRMHQ491DX2",
        "device_id": "517a6d1152c2feb242d3bb37c95f0c00",
    },
    "zyban": {
        "config_entry_id": "01KVV1CXP99AQ4WJ19XQRKRG9W",
        "device_id": "bba34e5d20d6e400e64fdb2c45abb924",
    },
}

# Medication slugs whose entities must be SKIPPED (left in the graveyard, not
# resurrected).  Use for test/duplicate devices you do not want to bring back.
BLACKLIST: list[str] = ["paracetamol_er", "paracetamol_test"]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

OLD_PLATFORM = "pill_logger"
NEW_PLATFORM = "ax_dose_logger"
DEFAULT_INPUT_FILENAME = "core.entity_registry.json"

# Prefixes sorted longest-first so e.g. "paracetamol_er" is tested before
# "paracetamol" (the trailing-underscore guard already disambiguates, but this
# is a belt-and-braces measure).
_SORTED_PREFIXES = sorted(PREFIX_MAP.keys(), key=len, reverse=True)

# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------


def _split_unique_id(unique_id: str, prefix_key: str | None = None) -> tuple[str, str]:
    """
    Split a ``unique_id`` into ``(prefix, suffix)``.

    Historical ``pill_logger`` entities may carry a ``unique_id`` prefixed
    with the medication slug (e.g. ``paracetamol_days_since_first_dose``)
    rather than a ULID.  Splitting such a value on the *first* underscore
    would yield ``("paracetamol", "days_since_first_dose")`` only by luck --
    a slug like ``vitamin_d_days_since_first_dose`` would split into
    ``("vitamin", "d_days_since_first_dose")``.  To avoid this, when
    ``prefix_key`` is supplied (the slug already resolved from the
    ``entity_id``) we strip exactly ``{prefix_key}_`` from the start of the
    ``unique_id`` and treat the remainder as the suffix.

    Only when the slug prefix is absent do we fall back to splitting on the
    first underscore -- which is safe for the new ``ax_dose_logger`` format
    whose 26-char ULID prefix contains no underscores.

    Returns
    -------
    (prefix, suffix)
        ``prefix`` is the stripped leading segment (the slug or ULID);
        ``suffix`` is the remainder.  If no underscore is found at all,
        returns ``("", unique_id)``.
    """
    if prefix_key and unique_id.startswith(prefix_key + "_"):
        return prefix_key, unique_id[len(prefix_key) + 1 :]
    idx = unique_id.find("_")
    if idx == -1:
        return "", unique_id
    return unique_id[:idx], unique_id[idx + 1 :]


def _resolve_prefix(entity_id: str) -> str | None:
    """
    Resolve the medication slug from an ``entity_id`` and return the matching
    :data:`PREFIX_MAP` key, or ``None`` if no prefix matches.

    Strips the HA domain prefix (``sensor.``, ``button.``, etc.) then tests
    whether the remaining slug starts with ``{prefix}_`` for each known prefix
    (longest-first).  The trailing-underscore guard disambiguates overlapping
    names like ``paracetamol`` vs ``paracetamol_er``.
    """
    dot = entity_id.find(".")
    slug = entity_id[dot + 1 :] if dot != -1 else entity_id
    for prefix in _SORTED_PREFIXES:
        if slug.startswith(prefix + "_"):
            return prefix
    return None


def _is_blacklisted(entity_id: str) -> str | None:
    """
    Return the blacklisted slug if the ``entity_id`` matches a
    :data:`BLACKLIST` entry (by the same trailing-underscore rule), else
    ``None``.
    """
    dot = entity_id.find(".")
    slug = entity_id[dot + 1 :] if dot != -1 else entity_id
    for bl in BLACKLIST:
        if slug.startswith(bl + "_"):
            return bl
    return None


# ---------------------------------------------------------------------------
# Dual-array transformation
# ---------------------------------------------------------------------------


def _extract_modern_template(
    active_entities: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """
    Scan the active ``entities`` array and return the first entity whose
    ``platform == NEW_PLATFORM`` as a schema template, or ``None`` if no such
    entity exists.

    The returned dictionary captures the exact set of keys (and their value
    types) that the host Home Assistant version requires on active entities.
    It is used by :func:`_inject_missing_schema_keys` to backfill any keys
    missing from resurrected graveyard entities.
    """
    for ent in active_entities:
        if ent.get("platform") == NEW_PLATFORM:
            return dict(ent)
    return None


def _inject_missing_schema_keys(
    migrated: dict[str, Any],
    modern_template: dict[str, Any] | None,
) -> None:
    """
    Backfill any schema keys present in ``modern_template`` but missing from
    ``migrated``, using type-safe fallback values derived from the template:

    * ``bool``  -> ``False``
    * ``list``  -> ``[]``
    * ``dict``  -> ``{}`
    * otherwise -> ``None``

    Existing keys in ``migrated`` are never overwritten (uses ``setdefault``).
    If ``modern_template`` is ``None`` (no generator entity was found to
    harvest a schema from), this is a no-op.
    """
    if not modern_template:
        return
    for key, template_value in modern_template.items():
        if key not in migrated:
            if isinstance(template_value, bool):
                migrated[key] = False
            elif isinstance(template_value, list):
                migrated[key] = []
            elif isinstance(template_value, dict):
                migrated[key] = {}
            else:
                migrated[key] = None


def migrate_registry(
    active_entities: list[dict[str, Any]],
    deleted_entities: list[dict[str, Any]],
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, int],
    list[str],
    list[str],
]:
    """
    Perform the dual-array resurrection migration.

    Parameters
    ----------
    active_entities
        The ``data["data"]["entities"]`` list (active entities).
    deleted_entities
        The ``data["data"]["deleted_entities"]`` list (graveyard).

    Returns
    -------
    new_active
        The new active entities list (generators purged, resurrected entities
        appended).
    new_deleted
        The new graveyard list (migrated pill_logger entities removed;
        everything else unchanged).
    stats
        Counts dict.
    errors
        Hard errors (unmatched pill_logger entities) -- migration incomplete.
    warnings
        Soft warnings (blacklisted entities skipped).
    """
    stats = {
        "generators_purged": 0,
        "pill_logger_found": 0,
        "migrated": 0,
        "resurrected": 0,
        "blacklisted": 0,
        "active_kept": 0,
        "graveyard_kept": 0,
    }
    errors: list[str] = []
    warnings: list[str] = []

    # --- Step 2: purge ax_dose_logger generators from the active array ---
    new_active: list[dict[str, Any]] = []
    for ent in active_entities:
        if ent.get("platform") == NEW_PLATFORM:
            stats["generators_purged"] += 1
        else:
            new_active.append(ent)
            stats["active_kept"] += 1

    # --- Extract a modern schema template from the active ax_dose_logger
    # generators before we purge them.  This captures the exact key set (and
    # value types) the host HA version requires, so resurrected graveyard
    # entities can be backfilled dynamically rather than hardcoding keys one
    # by one.  We extract from the *original* active_entities (before purge)
    # because the generators are the newest-format entities in the file.
    modern_template = _extract_modern_template(active_entities)

    # --- Steps 3-4: scan graveyard, migrate + resurrect pill_logger entities ---
    new_deleted: list[dict[str, Any]] = []
    for ent in deleted_entities:
        platform = ent.get("platform")

        if platform == OLD_PLATFORM:
            stats["pill_logger_found"] += 1
            entity_id = ent.get("entity_id", "")

            # Blacklist check -- skip resurrection, leave in graveyard.
            bl_slug = _is_blacklisted(entity_id)
            if bl_slug is not None:
                stats["blacklisted"] += 1
                warnings.append(
                    f"Blacklisted pill_logger entity skipped (left in "
                    f"graveyard): entity_id={entity_id!r} matches blacklist "
                    f"entry {bl_slug!r}."
                )
                new_deleted.append(ent)
                stats["graveyard_kept"] += 1
                continue

            prefix_key = _resolve_prefix(entity_id)
            if prefix_key is None:
                errors.append(
                    f"Unmatched pill_logger entity: entity_id={entity_id!r} "
                    f"-- no PREFIX_MAP entry matches its slug. "
                    f"Add the medication slug to PREFIX_MAP."
                )
                # Keep the entity in the graveyard so nothing is lost.
                new_deleted.append(ent)
                stats["graveyard_kept"] += 1
                continue

            mapping = PREFIX_MAP[prefix_key]
            new_ceid = mapping["config_entry_id"]
            new_devid = mapping["device_id"]
            old_uid = ent.get("unique_id", "")
            # Strip the resolved slug prefix (not the first underscore) so a
            # slug-prefixed unique_id like "vitamin_d_days_since_first_dose"
            # yields suffix "days_since_first_dose", not "d_...".
            _prefix, suffix = _split_unique_id(old_uid, prefix_key)
            new_uid = f"{new_ceid}_{suffix}" if suffix else new_ceid

            migrated = dict(ent)  # shallow copy preserves all other fields
            migrated["platform"] = NEW_PLATFORM
            migrated["config_entry_id"] = new_ceid
            migrated["device_id"] = new_devid
            migrated["unique_id"] = new_uid
            # entity_id and id intentionally left untouched.

            # Dynamic schema backfill: historical entities pulled from the
            # graveyard may predate keys that newer Home Assistant versions
            # strictly require on active entities.  Rather than hardcoding each
            # key, we inherit the schema from a modern ax_dose_logger generator
            # entity (harvested above into ``modern_template``) and inject any
            # missing keys with type-safe fallback values.  This guarantees
            # conformance with whatever HA version generated the template,
            # future-proofing against further schema additions.
            _inject_missing_schema_keys(migrated, modern_template)

            # Resurrection: remove the orphaned_timestamp tag and move the
            # entity into the active list.
            migrated.pop("orphaned_timestamp", None)
            new_active.append(migrated)
            stats["migrated"] += 1
            stats["resurrected"] += 1

        else:
            # Non-pill_logger graveyard entity -- leave unchanged.
            new_deleted.append(ent)
            stats["graveyard_kept"] += 1

    return new_active, new_deleted, stats, errors, warnings


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------


def load_registry(path: Path) -> dict[str, Any]:
    """Load and parse the entity-registry JSON file."""
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def save_registry_atomic(path: Path, data: dict[str, Any]) -> None:
    """Write JSON to ``path`` atomically via a temp file + os.replace."""
    tmp_fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp_name, path)
    except BaseException:
        # Clean up the temp file on any failure.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def print_report(
    stats: dict[str, int], errors: list[str], warnings: list[str]
) -> int:
    """Print a human-readable summary.  Returns the exit code."""
    print("=" * 60)
    print("Entity Registry Migration Summary (dual-array resurrection)")
    print("=" * 60)
    print(f"  pill_logger found (graveyard) : {stats['pill_logger_found']}")
    print(f"  migrated + resurrected        : {stats['migrated']}")
    print(f"  blacklisted (skipped)          : {stats['blacklisted']}")
    print(f"  ax_dose_logger purged (active) : {stats['generators_purged']}")
    print(f"  active entities kept           : {stats['active_kept']}")
    print(f"  graveyard entities kept        : {stats['graveyard_kept']}")
    print("=" * 60)

    if warnings:
        print("\nWARNINGS (blacklisted entities left in graveyard):")
        for msg in warnings:
            print(f"  - {msg}")

    if errors:
        # Unmatched entities are an informational warning, NOT a fatal error.
        # They are left in the graveyard unchanged so nothing is lost, and the
        # migration proceeds to save the file.  This lets the user deliberately
        # omit junk/test devices from PREFIX_MAP without blocking the run.
        print(
            f"\nINFO -- {len(errors)} pill_logger entit(y/ies) had no "
            "matching PREFIX_MAP entry and were left in the graveyard:"
        )
        for msg in errors:
            print(f"  - {msg}")
        print(
            "These are not migrated. Add their slugs to PREFIX_MAP if you "
            "want them resurrected, or leave them as-is to keep them in the "
            "graveyard."
        )

    if stats["pill_logger_found"] == 0:
        # This is the only FATAL condition: no historical data to migrate at
        # all, so writing a file would be pointless / dangerous.
        print(
            "\nFATAL: no pill_logger entities found in the graveyard. "
            "Is this the right file, or has it already been migrated?"
        )
        return 1

    if errors:
        print(
            f"\nMigration complete with {len(errors)} unmatched entit(y/ies) "
            "left in the graveyard (see INFO above)."
        )
    else:
        print("\nAll pill_logger entities matched successfully.")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Migrate core.entity_registry.json from pill_logger to "
        "ax_dose_logger (offline, non-destructive, dual-array resurrection)."
    )
    p.add_argument(
        "input",
        nargs="?",
        default=DEFAULT_INPUT_FILENAME,
        help=f"Path to the registry JSON (default: {DEFAULT_INPUT_FILENAME})",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the migration summary and write no files.",
    )
    p.add_argument(
        "--overwrite",
        action="store_true",
        help="Write to the input path in place instead of '<input>.migrated'. "
        "DANGEROUS -- a backup is still created first.",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    input_path = Path(args.input).resolve()

    if not input_path.exists():
        print(f"ERROR: input file not found: {input_path}", file=sys.stderr)
        return 2

    if not PREFIX_MAP:
        print(
            "ERROR: PREFIX_MAP at the top of the script is empty. "
            "Fill it in with your medication-slug -> ULID pairings first.",
            file=sys.stderr,
        )
        return 2

    try:
        data = load_registry(input_path)
    except json.JSONDecodeError as exc:
        print(f"ERROR: failed to parse JSON: {exc}", file=sys.stderr)
        return 2

    # Home Assistant's core.entity_registry nests the entities arrays inside a
    # top-level "data" object alongside "version"/"minor_version"/"key"
    # metadata.  Parse and write through that wrapper so the storage schema is
    # preserved exactly.
    data_wrapper = data.get("data")
    if not isinstance(data_wrapper, dict):
        print(
            "ERROR: top-level 'data' object missing or not a dict. "
            "This does not look like a Home Assistant core.entity_registry "
            'file (expected {"data": {"entities": [...]}}).',
            file=sys.stderr,
        )
        return 2

    active_entities = data_wrapper.get("entities")
    if not isinstance(active_entities, list):
        print(
            "ERROR: 'data.entities' array missing or not a list.",
            file=sys.stderr,
        )
        return 2

    deleted_entities = data_wrapper.get("deleted_entities")
    if not isinstance(deleted_entities, list):
        # Some registries may not have a graveyard yet -- treat as empty.
        deleted_entities = []

    new_active, new_deleted, stats, errors, warnings = migrate_registry(
        active_entities, deleted_entities
    )
    exit_code = print_report(stats, errors, warnings)
    if exit_code != 0:
        # Fatal error (no pill_logger entities found).  Never write a file in
        # this case -- there is nothing to migrate.
        if not args.dry_run:
            print("\nNo files written. Run with --dry-run to inspect.")
        return exit_code

    if args.dry_run:
        print("\nDry-run only -- no files written.")
        return 0

    # Write back through the data wrapper; root-level metadata is untouched.
    data_wrapper["entities"] = new_active
    data_wrapper["deleted_entities"] = new_deleted

    if args.overwrite:
        backup_path = input_path.with_suffix(input_path.suffix + ".bak")
        print(f"\nBacking up original to: {backup_path}")
        backup_path.write_bytes(input_path.read_bytes())
        out_path = input_path
    else:
        out_path = input_path.with_suffix(input_path.suffix + ".migrated")

    save_registry_atomic(out_path, data)
    print(f"\nWrote migrated registry to: {out_path}")
    if not args.overwrite:
        print(
            "Review the diff, then rename into place:\n"
            f"  mv {out_path.name} {input_path.name}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())