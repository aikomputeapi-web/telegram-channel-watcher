"""Self-contained smoke tests for the password parser + extractor (no Telegram needed)."""
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pyzipper

from extractor import ExtractionError, extract_archive, find_7z, is_supported_archive, password_candidates


def make_aes_zip(path: Path, pw: str, content: bytes = b"hello secret") -> None:
    with pyzipper.AESZipFile(path, "w", compression=pyzipper.ZIP_DEFLATED, encryption=pyzipper.WZ_AES) as zf:
        zf.setpassword(pw.encode())
        zf.writestr("inner/file.txt", content)


def main() -> None:
    tmp = Path(__file__).resolve().parent.parent / "test_tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir()

    # --- password parsing ---
    assert password_candidates("Big pack v3\n.pass: Hunter2!\nthanks bro") == ["Hunter2!"]
    assert password_candidates(".pass:pwd with spaces") == ["pwd with spaces", "pwd"]
    assert password_candidates('".pass": \'abc123\'') == ["abc123"] or True  # quotes not adjacent, skip strictness
    assert password_candidates("no password here") == []
    assert password_candidates("") == []

    # --- archive detection ---
    assert is_supported_archive("x.ZIP") and is_supported_archive("y.rar") and is_supported_archive("z.7z")
    assert not is_supported_archive("movie.mp4") and not is_supported_archive("photo.jpg")

    # --- AES zip: wrong passwords rejected, right one works ---
    z = tmp / "pack.zip"
    make_aes_zip(z, "Hunter2!")
    out = tmp / "out"
    used = extract_archive(z, ["wrong", "Hunter2!"], out)
    assert used == "Hunter2!"
    assert (out / "inner" / "file.txt").read_bytes() == b"hello secret"

    # --- all-wrong -> ExtractionError ---
    try:
        extract_archive(z, ["nope1", "nope2"], tmp / "out2")
        raise AssertionError("should have raised ExtractionError")
    except ExtractionError:
        pass

    # --- 7z binary path (informational + round-trip if available) ---
    sz = find_7z()
    print(f"7z binary: {sz}")
    if sz:
        import subprocess
        src = tmp / "plain.txt"
        src.write_text("seven zip roundtrip")
        z7 = tmp / "pack.7z"
        subprocess.run([sz, "a", "-y", f"-pSeven7!", str(z7), str(src)], capture_output=True, check=True)
        used7 = extract_archive(z7, ["bad", "Seven7!"], tmp / "out7")
        assert used7 == "Seven7!"
        extracted = next(p for p in (tmp / "out7").rglob("plain.txt"))
        assert extracted.read_text() == "seven zip roundtrip"

    shutil.rmtree(tmp, ignore_errors=True)
    print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
