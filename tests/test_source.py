"""Source normalisation, hashing and deterministic writing (Architecture §4.2, §5.1, §0 rule 7)."""
import pytest

from iotc.source import decode_source, write_text

TEXT = "SENSOR temp AT A0 ANALOG UNIT C RANGE -40..125;\nRULE r: WHEN temp > 35 THEN fan ON;\n"


@pytest.mark.parametrize("variant", [
    TEXT.encode(),
    b"\xef\xbb\xbf" + TEXT.encode(),
    TEXT.replace("\n", "\r\n").encode(),
    TEXT.replace("\n", "\r").encode(),
    b"\xef\xbb\xbf" + TEXT.replace("\n", "\r\n").encode(),
])
def test_normalisation_gives_identical_text_and_hash(variant):
    ref = decode_source("a.iot", TEXT.encode())
    src = decode_source("a.iot", variant)
    assert src.text == ref.text
    assert src.sha256 == ref.sha256
    assert src.issue is None


def test_invalid_utf8_reports_position():
    data = b"SENSOR temp AT A0;\n// caf\xe9 comment\n"
    src = decode_source("a.iot", data)
    assert src.issue is not None
    assert (src.issue.line, src.issue.col) == (2, 7)


def test_write_text_lf_and_single_trailing_newline(tmp_path):
    p = tmp_path / "x" / "out.txt"
    write_text(p, "a\nb\n\n\n")
    assert p.read_bytes() == b"a\nb\n"
    with pytest.raises(ValueError):
        write_text(p, "a\r\nb")
