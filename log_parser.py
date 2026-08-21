"""Optional hook into the offline stealer log parser (../stealer-log-parser).

After the watcher extracts an archive, `parse_extracted_dir` runs the parser
over the extracted files and writes the normalized outputs (records.jsonl,
summary.csv, report.md, manifest.json, diagnostics.jsonl) next to them.

The parser package is a no-dependency install; if it is missing this module
degrades gracefully and the watcher keeps working without parsing.
"""
from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger("log_parser")

try:
    from stealer_log_parser.config import ParserConfig
    from stealer_log_parser.models import RedactionProfile, StealerFamily
    from stealer_log_parser.pipeline import PipelineError, run_parse

    PARSER_AVAILABLE = True
except ImportError:
    PARSER_AVAILABLE = False

INSTALL_HINT = "pip install -e ..\\stealer-log-parser   (or set PARSE_LOGS=0 to disable)"


def parser_available() -> bool:
    return PARSER_AVAILABLE


def _redaction(value: str) -> RedactionProfile:
    try:
        return RedactionProfile(value.strip().lower())
    except ValueError:
        log.warning("ignoring unknown PARSER_REDACTION %r; using 'none'", value)
        return RedactionProfile.NONE


def _family(value: str) -> StealerFamily | None:
    value = value.strip().lower()
    if not value or value == "auto":
        return None
    try:
        return StealerFamily(value)
    except ValueError:
        log.warning("ignoring unknown PARSER_FAMILY %r; using auto", value)
        return None


def parse_extracted_dirs(
    source_dirs: list[Path],
    output_dir: Path,
    *,
    redaction: str = "none",
    family: str = "auto",
) -> dict | None:
    """Parse the extracted contents of `source_dirs`, writing outputs into
    `output_dir`. Returns a short summary dict, or None when the parser package
    is not installed. Never raises for parser problems."""
    if not PARSER_AVAILABLE:
        log.warning("stealer-log-parser not installed - install with: %s", INSTALL_HINT)
        return None

    config = ParserConfig(
        output_dir=Path(output_dir),
        redaction=_redaction(redaction),
        family_override=_family(family),
        overwrite=True,
        allow_insecure_permissions=True,
        quiet=True,
    )
    try:
        summary = run_parse(source_dirs, config)
    except PipelineError as exc:
        log.warning("msg parse failed (%s): %s", exc.code, exc.message)
        return None
    except Exception as exc:
        log.warning("msg parse failed unexpectedly: %s", exc)
        return None

    result = {
        "record_count": summary.record_count,
        "family": summary.family,
        "family_score": summary.family_score,
        "accepted_files": summary.accepted_files,
        "skipped_files": summary.skipped_files,
        "exit_code": summary.exit_code,
    }
    if summary.exit_code != 0:
        log.warning("msg parse completed with exit code %d", summary.exit_code)
    return result


def parse_extracted_dir(
    source_dir: Path,
    output_dir: Path,
    *,
    redaction: str = "none",
    family: str = "auto",
) -> dict | None:
    """Convenience wrapper parsing a single extracted directory."""
    return parse_extracted_dirs(
        [Path(source_dir)],
        Path(output_dir),
        redaction=redaction,
        family=family,
    )
