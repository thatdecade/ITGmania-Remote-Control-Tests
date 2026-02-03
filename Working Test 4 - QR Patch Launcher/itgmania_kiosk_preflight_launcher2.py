#!/usr/bin/env python3
"""
ITGmania Simply Love kiosk preflight launcher.

Goals:
- Keep mod payloads (files/snippets) outside this script in a mod source folder.
- Apply patches only when needed (idempotent).
- Backup originals before modifying anything.
- Record what changed in a JSON manifest (for uninstall and auditing).
- Launch Program/ITGmania.exe by default.

Folder layout (default):
  itgmania_kiosk_preflight_launcher2.py
  mods/
    simplylove_screenkiosk/
      assets/
        metrics_screenkiosk_block.ini
      Themes/
        Simply Love/
          BGAnimations/
            ScreenKiosk overlay/
              default.lua

Notes:
- Everything under mods/simplylove_screenkiosk/ is treated as patch files and copied
  into the ITGmania folder (same relative path) when needed, except assets/.
- metrics.ini is patched (surgically) instead of being replaced.

Usage:
  python itgmania_kiosk_preflight_launcher2.py "C:\\Games\\ITGmania"
  python itgmania_kiosk_preflight_launcher2.py "C:\\Games\\ITGmania" --uninstall
  python itgmania_kiosk_preflight_launcher2.py "C:\\Games\\ITGmania" --no-launch
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def utc_timestamp_seconds() -> str:
    """Returns an ISO-8601 UTC timestamp with second precision and a trailing Z."""
    return (
        datetime.datetime.now(datetime.timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


MOD_IDENTIFIER = "simplylove_screenkiosk"
STATE_FOLDER_NAME = "preflight_backups"
MANIFEST_FILE_NAME = "manifest.json"

EXCLUDED_MOD_SOURCE_PATH_PARTS = {"assets", ".git", ".svn", "__pycache__"}
EXCLUDED_MOD_SOURCE_FILE_NAMES = {".DS_Store", "Thumbs.db"}



@dataclass(frozen=True)
class FileCopyOutcome:
    changed: bool
    change_kind: str  # "none", "modify", "create"
    destination_relative_path: str


def parse_arguments() -> argparse.Namespace:
    argument_parser = argparse.ArgumentParser(description="ITGmania Simply Love kiosk preflight launcher.")
    argument_parser.add_argument("itgmania_root", help="Path to the ITGmania folder (the one containing Program/).")
    argument_parser.add_argument(
        "--uninstall",
        action="store_true",
        help="Uninstall the mod by restoring backups from the manifest.",
    )
    argument_parser.add_argument(
        "--no-launch",
        action="store_true",
        help="Run preflight only. Do not launch ITGmania.",
    )
    argument_parser.add_argument(
        "--mod-source-root",
        default=None,
        help="Path to the mod source folder. Default is: <script_folder>/mods/simplylove_screenkiosk",
    )
    argument_parser.add_argument(
        "--state-root",
        default=None,
        help="Where to store backups and the manifest. Default is: <itgmania_root>/preflight_backups/simplylove_screenkiosk",
    )
    return argument_parser.parse_args()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def read_text_utf8(file_path: Path) -> str:
    return file_path.read_bytes().decode("utf-8")


def write_text_utf8(file_path: Path, text: str) -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_bytes(text.encode("utf-8"))


def detect_newline_sequence(text: str) -> str:
    if "\r\n" in text:
        return "\r\n"
    return "\n"


def section_ranges_by_name(lines_with_endings: List[str]) -> Dict[str, Tuple[int, int]]:
    section_header_pattern = re.compile(r"^\s*\[([^\]]+)\]\s*$")
    ranges: Dict[str, Tuple[int, int]] = {}

    header_indices: List[Tuple[str, int]] = []
    for line_index, line in enumerate(lines_with_endings):
        match = section_header_pattern.match(line.strip())
        if match:
            section_name = match.group(1)
            header_indices.append((section_name, line_index))

    for current_index, (section_name, start_index) in enumerate(header_indices):
        if current_index + 1 < len(header_indices):
            end_index = header_indices[current_index + 1][1]
        else:
            end_index = len(lines_with_endings)
        ranges[section_name] = (start_index, end_index)

    return ranges


def ensure_common_initial_screen(
    lines_with_endings: List[str],
    newline_sequence: str,
) -> Tuple[List[str], bool]:
    ranges = section_ranges_by_name(lines_with_endings)
    if "Common" not in ranges:
        raise RuntimeError('metrics.ini is missing a [Common] section. Aborting to avoid corruption.')

    common_start, common_end = ranges["Common"]
    changed = False

    initial_screen_line_index: Optional[int] = None
    for line_index in range(common_start + 1, common_end):
        if lines_with_endings[line_index].lstrip().startswith("InitialScreen="):
            initial_screen_line_index = line_index
            break

    desired_line = 'InitialScreen="ScreenKiosk"' + newline_sequence

    if initial_screen_line_index is None:
        lines_with_endings.insert(common_start + 1, desired_line)
        changed = True
    else:
        if lines_with_endings[initial_screen_line_index].strip() != desired_line.strip():
            lines_with_endings[initial_screen_line_index] = desired_line
            changed = True

    return lines_with_endings, changed


def ensure_screen_kiosk_section(
    lines_with_endings: List[str],
    newline_sequence: str,
    kiosk_section_block_text: str,
) -> Tuple[List[str], bool]:
    ranges = section_ranges_by_name(lines_with_endings)
    desired_block_lines = [(line + newline_sequence) for line in kiosk_section_block_text.splitlines()]

    def find_insertion_index() -> int:
        refreshed_ranges = section_ranges_by_name(lines_with_endings)
        if "ScreenAttract" in refreshed_ranges:
            return refreshed_ranges["ScreenAttract"][0]
        return len(lines_with_endings)

    changed = False

    if "ScreenKiosk" in ranges:
        section_start, section_end = ranges["ScreenKiosk"]
        existing_block = "".join(lines_with_endings[section_start:section_end]).strip()
        desired_block = "".join(desired_block_lines).strip()
        if existing_block != desired_block:
            lines_with_endings[section_start:section_end] = desired_block_lines + [newline_sequence]
            changed = True
    else:
        insertion_index = find_insertion_index()
        block_to_insert = desired_block_lines + [newline_sequence]
        lines_with_endings[insertion_index:insertion_index] = block_to_insert
        changed = True

    return lines_with_endings, changed


def metrics_file_has_kiosk_mod(metrics_text: str) -> bool:
    required_substrings = [
        'InitialScreen="ScreenKiosk"',
        "[ScreenKiosk]",
        'NextScreen="ScreenSelectMusicCasual"',
        'Class="ScreenAttract"',
    ]
    return all(substring in metrics_text for substring in required_substrings)


def compute_patched_metrics_text(metrics_text: str, kiosk_section_block_text: str) -> Tuple[bool, str]:
    if metrics_file_has_kiosk_mod(metrics_text):
        return False, metrics_text

    newline_sequence = detect_newline_sequence(metrics_text)
    lines_without_endings = metrics_text.splitlines(keepends=False)
    lines_with_endings = [(line + newline_sequence) for line in lines_without_endings]

    if not lines_with_endings or not lines_with_endings[-1].endswith(newline_sequence):
        lines_with_endings.append(newline_sequence)

    lines_with_endings, common_changed = ensure_common_initial_screen(lines_with_endings, newline_sequence)
    lines_with_endings, kiosk_section_changed = ensure_screen_kiosk_section(
        lines_with_endings,
        newline_sequence,
        kiosk_section_block_text,
    )

    changed = common_changed or kiosk_section_changed
    patched_text = "".join(lines_with_endings)

    if not changed:
        return False, metrics_text

    return True, patched_text


def compute_backup_path(state_root_path: Path, destination_relative_path: str) -> Path:
    destination_relative = Path(destination_relative_path)
    backup_base_path = state_root_path / "backups" / destination_relative
    return backup_base_path.with_name(backup_base_path.name + ".bak")


def backup_file_once(source_file_path: Path, backup_file_path: Path) -> None:
    backup_file_path.parent.mkdir(parents=True, exist_ok=True)
    if backup_file_path.exists():
        return
    shutil.copy2(source_file_path, backup_file_path)


def load_manifest(manifest_file_path: Path) -> Dict[str, Any]:
    if not manifest_file_path.exists():
        return {
            "mod_identifier": MOD_IDENTIFIER,
            "created_utc": utc_timestamp_seconds(),
            "files": [],
        }
    return json.loads(manifest_file_path.read_text(encoding="utf-8"))


def save_manifest(manifest_file_path: Path, manifest_data: Dict[str, Any]) -> None:
    manifest_file_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_file_path.write_text(
        json.dumps(manifest_data, indent=2, sort_keys=True),
        encoding="utf-8",
        newline="\n",
    )


def record_file_change(
    manifest_data: Dict[str, Any],
    destination_file_path: Path,
    destination_relative_path: str,
    backup_file_path: Optional[Path],
    change_kind: str,
    sha256_before: Optional[str],
    sha256_after: Optional[str],
) -> None:
    normalized_destination = str(destination_file_path)

    existing_entries: List[Dict[str, Any]] = manifest_data.get("files", [])
    for entry in existing_entries:
        if entry.get("destination_path") == normalized_destination:
            return

    existing_entries.append(
        {
            "destination_path": normalized_destination,
            "destination_relative_path": destination_relative_path,
            "backup_path": str(backup_file_path) if backup_file_path else None,
            "change_kind": change_kind,
            "sha256_before": sha256_before,
            "sha256_after": sha256_after,
            "changed_utc": utc_timestamp_seconds(),
        }
    )
    manifest_data["files"] = existing_entries


def resolve_mod_source_root(arguments: argparse.Namespace) -> Path:
    if arguments.mod_source_root:
        return Path(arguments.mod_source_root).expanduser().resolve()
    script_folder_path = Path(__file__).resolve().parent
    return script_folder_path / "mods" / MOD_IDENTIFIER


def resolve_state_root(itgmania_root_path: Path, arguments: argparse.Namespace) -> Path:
    if arguments.state_root:
        return Path(arguments.state_root).expanduser().resolve()
    return itgmania_root_path / STATE_FOLDER_NAME / MOD_IDENTIFIER


def copy_mod_files_from_source_tree(
    itgmania_root_path: Path,
    mod_source_root_path: Path,
    state_root_path: Path,
    manifest_data: Dict[str, Any],
) -> List[FileCopyOutcome]:
    """
    Copies mod payload files into the ITGmania folder, preserving relative paths.

    This treats the mod source folder as an overlay of the ITGmania root.
    Everything under <mod_source_root>/ is copied, except excluded folders and files
    like assets/ (used for patch snippets).
    """
    if not mod_source_root_path.exists():
        raise RuntimeError(f"Missing mod source folder: {mod_source_root_path}")

    outcomes: List[FileCopyOutcome] = []

    for source_file_path in mod_source_root_path.rglob("*"):
        if not source_file_path.is_file():
            continue

        if source_file_path.name in EXCLUDED_MOD_SOURCE_FILE_NAMES:
            continue

        relative_path_from_mod_root = source_file_path.relative_to(mod_source_root_path)
        if any(part in EXCLUDED_MOD_SOURCE_PATH_PARTS for part in relative_path_from_mod_root.parts):
            continue

        destination_file_path = itgmania_root_path / relative_path_from_mod_root
        destination_relative_path = relative_path_from_mod_root.as_posix()

        desired_bytes = source_file_path.read_bytes()
        desired_sha256 = sha256_bytes(desired_bytes)

        if destination_file_path.exists():
            existing_bytes = destination_file_path.read_bytes()
            if existing_bytes == desired_bytes:
                outcomes.append(FileCopyOutcome(False, "none", destination_relative_path))
                continue

            backup_file_path = compute_backup_path(state_root_path, destination_relative_path)
            backup_file_once(destination_file_path, backup_file_path)

            destination_file_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_file_path, destination_file_path)

            record_file_change(
                manifest_data,
                destination_file_path,
                destination_relative_path,
                backup_file_path,
                "modify",
                sha256_before=sha256_bytes(existing_bytes),
                sha256_after=desired_sha256,
            )
            outcomes.append(FileCopyOutcome(True, "modify", destination_relative_path))
            continue

        destination_file_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_file_path, destination_file_path)

        record_file_change(
            manifest_data,
            destination_file_path,
            destination_relative_path,
            backup_file_path=None,
            change_kind="create",
            sha256_before=None,
            sha256_after=desired_sha256,
        )
        outcomes.append(FileCopyOutcome(True, "create", destination_relative_path))

    return outcomes

def install_or_repair_mod(itgmania_root_path: Path, mod_source_root_path: Path, state_root_path: Path) -> None:
    metrics_destination_relative_path = "Themes/Simply Love/metrics.ini"
    metrics_destination_path = itgmania_root_path / Path(metrics_destination_relative_path)

    metrics_block_source_path = mod_source_root_path / "assets" / "metrics_screenkiosk_block.ini"

    if not metrics_destination_path.exists():
        raise RuntimeError(f"Missing required file: {metrics_destination_path}")
    if not metrics_block_source_path.exists():
        raise RuntimeError(f"Missing mod source file: {metrics_block_source_path}")

    kiosk_section_block_text = read_text_utf8(metrics_block_source_path)

    manifest_file_path = state_root_path / MANIFEST_FILE_NAME
    manifest_data = load_manifest(manifest_file_path)

    # Step 1: Patch metrics.ini (backup before modify)
    metrics_original_text = read_text_utf8(metrics_destination_path)
    metrics_changed, metrics_patched_text = compute_patched_metrics_text(metrics_original_text, kiosk_section_block_text)
    if not metrics_changed:
        print("metrics.ini already configured for ScreenKiosk.")
    else:
        metrics_backup_path = compute_backup_path(state_root_path, metrics_destination_relative_path)
        backup_file_once(metrics_destination_path, metrics_backup_path)
        write_text_utf8(metrics_destination_path, metrics_patched_text)
        record_file_change(
            manifest_data,
            metrics_destination_path,
            metrics_destination_relative_path,
            metrics_backup_path,
            "modify",
            sha256_before=sha256_text(metrics_original_text),
            sha256_after=sha256_text(metrics_patched_text),
        )
        print("metrics.ini patched.")

    # Step 2: Copy mod payload files (overlay mod source onto ITGmania root)
    copy_outcomes = copy_mod_files_from_source_tree(itgmania_root_path, mod_source_root_path, state_root_path, manifest_data)

    overlay_relative_path = "Themes/Simply Love/BGAnimations/ScreenKiosk overlay/default.lua"
    overlay_changed = [outcome for outcome in copy_outcomes if outcome.destination_relative_path == overlay_relative_path]

    if overlay_changed:
        if overlay_changed[0].change_kind == "create":
            print("ScreenKiosk overlay created.")
        elif overlay_changed[0].change_kind == "modify":
            print("ScreenKiosk overlay updated.")
    else:
        overlay_source_path = mod_source_root_path / Path(overlay_relative_path)
        if overlay_source_path.exists():
            print("ScreenKiosk overlay already present.")

    save_manifest(manifest_file_path, manifest_data)


def uninstall_mod(state_root_path: Path) -> None:
    manifest_file_path = state_root_path / MANIFEST_FILE_NAME
    if not manifest_file_path.exists():
        print("No manifest found. Nothing to uninstall.")
        return

    manifest_data = load_manifest(manifest_file_path)
    file_entries: List[Dict[str, Any]] = manifest_data.get("files", [])

    for entry in file_entries:
        destination_path = Path(entry["destination_path"])
        backup_path_value = entry.get("backup_path")
        change_kind = entry.get("change_kind")

        if change_kind == "modify":
            if not backup_path_value:
                raise RuntimeError(f"Manifest entry missing backup_path for modified file: {destination_path}")
            backup_path = Path(backup_path_value)
            if not backup_path.exists():
                raise RuntimeError(f"Backup file missing: {backup_path}")
            destination_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup_path, destination_path)
            print(f"Restored: {destination_path}")

        elif change_kind == "create":
            if destination_path.exists():
                destination_path.unlink()
                print(f"Removed: {destination_path}")

        else:
            raise RuntimeError(f"Unknown change_kind in manifest: {change_kind}")

    print("Uninstall complete.")


def launch_itgmania(itgmania_root_path: Path) -> None:
    executable_path = itgmania_root_path / "Program" / "ITGmania.exe"
    if not executable_path.exists():
        raise RuntimeError(f"ITGmania executable not found: {executable_path}")
    subprocess.Popen([str(executable_path)], cwd=str(executable_path.parent))


def main() -> None:
    arguments = parse_arguments()
    itgmania_root_path = Path(arguments.itgmania_root).expanduser().resolve()

    mod_source_root_path = resolve_mod_source_root(arguments)
    state_root_path = resolve_state_root(itgmania_root_path, arguments)

    if arguments.uninstall:
        uninstall_mod(state_root_path)
    else:
        install_or_repair_mod(itgmania_root_path, mod_source_root_path, state_root_path)

    if not arguments.no_launch:
        launch_itgmania(itgmania_root_path)


if __name__ == "__main__":
    main()
