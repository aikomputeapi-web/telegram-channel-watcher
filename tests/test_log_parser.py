"""Smoke tests for the optional stealer-log-parser hook (no parser install needed)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from log_parser import PARSER_AVAILABLE, parser_available, parse_extracted_dir


def main() -> None:
    # Import + graceful degradation must work even without the parser installed.
    assert parser_available() == PARSER_AVAILABLE
    src = Path(__file__).resolve().parent.parent / "test_tmp_parser"
    src.mkdir(exist_ok=True)
    (src / "placeholder.txt").write_text("nothing to parse")

    result = parse_extracted_dir(src, src, redaction="none", family="auto")
    if PARSER_AVAILABLE:
        assert isinstance(result, dict) and "record_count" in result
    else:
        assert result is None  # no crash, returns None without the package

    (src / "placeholder.txt").unlink(missing_ok=True)
    src.rmdir()
    print("LOG PARSER TESTS PASSED")

if __name__ == "__main__":
    main()
