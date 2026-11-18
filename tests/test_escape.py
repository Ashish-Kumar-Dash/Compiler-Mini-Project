"""Round-trip tests for iotc.escape (Architecture §11.4, §9.8, §4.2, §13.3)."""
from __future__ import annotations

import ast
import csv
import io
import json
import random
import shutil
import subprocess
from pathlib import Path

import pytest

from iotc.escape import c_comment_safe, cpp_str, csv_field, json_for_script, py_str

NASTY = [
    "C:\\",
    'a"b,c',
    "??/",
    "??=",
    "</script><img src=x onerror=alert(1)>",
    "h\u00e9llo \u2713",
    "\u2028",
    "\x00 tab\t nl\n",
    "?",
    "\\?",
    "",
    # adversarial additions: digits after an octal escape, trigraph-ish runs, quotes/backslashes
    "\x007",
    "\x01234",
    "\n0\t9\x7f8",
    "\u00e90",
    "???????",
    "??(??)??<??>??!??'??-",
    "?\\?\"?",
    '""',
    "\\\\\\",
    "\\x41\\101\\u0041",
    "*/ // /* #include",
    "\U0001f600\ufeff\u2029\x85\r\r\n",
    "\x00",
    "\x00\x00\x001",
]

# Alphabet pools for random strings; surrogates are excluded (not encodable as UTF-8).
_ASCII = [chr(c) for c in range(0x20, 0x7F)]
_HOT = list('\\"?01789xX') + ["??", "\\?", "?/"]
_CONTROL = [chr(c) for c in range(0x00, 0x20)] + ["\x7f"]
_NON_ASCII = ["\u00e9", "\u00ff", "\u0100", "\u2013", "\u2028", "\u2029", "\u2713", "\u4e2d",
              "\ufeff", "\uffff", "\U0001f600", "\U0010ffff", "\x80", "\x85", "\xa0"]


def _random_string(r: random.Random) -> str:
    pools = (_ASCII, _HOT, _CONTROL, _NON_ASCII)
    weights = (5, 3, 2, 2)
    n = r.randint(0, 24)
    return "".join(r.choice(r.choices(pools, weights)[0]) for _ in range(n))


def _corpus() -> list[str]:
    r = random.Random(20261118)
    return NASTY + [_random_string(r) for _ in range(200)]


CORPUS = _corpus()


# ---------------------------------------------------------------------------------------------
# C++ (§11.4): compile all literals into one program with WSL g++ and compare the bytes.


def _wsl_sh(script: str, *args: str, cwd: Path | None = None, timeout: float = 120):
    """Run ``sh -c script`` inside WSL; ``args`` become $1, $2, ..."""
    return subprocess.run(
        ["wsl", "-e", "sh", "-c", script, "sh", *args],
        capture_output=True,
        timeout=timeout,
        cwd=cwd,
    )


@pytest.fixture(scope="module")
def wsl_gxx() -> None:
    """Skip unless WSL and its g++ are usable (probed once per module)."""
    if shutil.which("wsl") is None:
        pytest.skip("wsl not available")
    try:
        probe = _wsl_sh("command -v g++ >/dev/null && g++ --version", timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        pytest.skip(f"wsl not usable: {exc}")
    if probe.returncode != 0:
        pytest.skip("g++ not available in WSL")


def test_cpp_str_shape():
    for s in CORPUS:
        lit = cpp_str(s)
        assert lit.startswith('"') and lit.endswith('"') and len(lit) >= 2
        assert lit.isascii() and lit.isprintable()
        assert "??" not in lit and "\\x" not in lit.replace("\\\\", "")
    assert cpp_str("") == '""'
    assert cpp_str("C:\\") == '"C:\\\\"'
    assert cpp_str('a"b?') == '"a\\"b\\?"'
    assert cpp_str("\x007") == '"\\0007"'
    assert cpp_str("\u00e9") == '"\\303\\251"'


def test_cpp_str_rejects_lone_surrogate():
    with pytest.raises(ValueError):
        cpp_str("a\ud800b")


@pytest.mark.diff
def test_cpp_str_round_trip_gxx(wsl_gxx, tmp_path):
    lines = [
        "#include <cstdio>",
        "#include <cstddef>",
        "static void dump(const char* p, std::size_t n) {",
        "  for (std::size_t i = 0; i < n; ++i) std::printf(\"%02x\", (unsigned)(unsigned char)p[i]);",
        "  std::printf(\"|\\n\");",
        "}",
        "#define DUMP(lit) dump(lit, sizeof(lit) - 1)",
        "int main() {",
    ]
    lines += [f"  DUMP({cpp_str(s)});" for s in CORPUS]
    lines += ["  return 0;", "}", ""]
    (tmp_path / "lits.cpp").write_bytes("\n".join(lines).encode("ascii"))
    proc = _wsl_sh(
        'cd "$(wslpath -a "$1")" && g++ -std=c++11 -Wall -Wextra -Werror -o lits lits.cpp && ./lits',
        str(tmp_path),
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    out = proc.stdout.decode("ascii").split("\n")
    assert out[-1] == "" and len(out) == len(CORPUS) + 1
    for s, row in zip(CORPUS, out):
        assert row.endswith("|")
        assert bytes.fromhex(row[:-1]) == s.encode("utf-8"), (s, cpp_str(s))


# ---------------------------------------------------------------------------------------------
# Python (§11.4)


def test_py_str_round_trip():
    for s in CORPUS + ["a\ud800b"]:
        lit = py_str(s)
        assert lit == repr(s)
        assert "\n" not in lit and "\r" not in lit
        assert ast.literal_eval(lit) == s


# ---------------------------------------------------------------------------------------------
# CSV (§9.8: NT,<t>,<rule>,"<text>")


def test_csv_field_nt_line():
    for s in CORPUS:
        if "\n" in s or "\r" in s:
            continue
        line = "NT,1000,alert," + csv_field(s)
        rows = list(csv.reader([line]))
        assert rows == [["NT", "1000", "alert", s]], (s, line)


def test_csv_field_full_text_with_newlines():
    text = "".join(f"NT,{i},alert,{csv_field(s)}\n" for i, s in enumerate(CORPUS))
    rows = list(csv.reader(io.StringIO(text, newline="")))
    assert rows == [["NT", str(i), "alert", s] for i, s in enumerate(CORPUS)]


def test_csv_field_examples():
    assert csv_field("") == '""'
    assert csv_field('a"b,c') == '"a""b,c"'


# ---------------------------------------------------------------------------------------------
# JSON inside HTML (§11.4)

_FORBIDDEN = ("<", ">", "&", "\u2028", "\u2029")


def test_json_for_script_round_trip():
    for i, s in enumerate(CORPUS):
        obj = {
            s: [s, {"k": s, "n": i, "f": 22.5, "b": True, "z": None}],
            "list": [s, [s, [s]]],
            "</script>": "&amp;<!--",
        }
        text = json_for_script(obj)
        for ch in _FORBIDDEN:
            assert ch not in text, (s, text)
        assert json.loads(text) == obj
    for s in CORPUS:
        assert json.loads(json_for_script(s)) == s


def test_json_for_script_examples():
    assert json_for_script({"a": "<b>&</b>"}) == '{"a":"\\u003cb\\u003e\\u0026\\u003c/b\\u003e"}'
    assert json_for_script("\\<") == '"\\\\\\u003c"'
    assert json_for_script("\u2028\u2029") == '"\\u2028\\u2029"'
    assert json_for_script("h\u00e9") == '"h\u00e9"'


def test_json_for_script_rejects_nan():
    with pytest.raises(ValueError):
        json_for_script(float("nan"))


# ---------------------------------------------------------------------------------------------
# Names in generated comments (§11.3)


@pytest.mark.parametrize("name", ["temp", "heater_2", "a", "0", "x__y"])
def test_c_comment_safe_accepts(name):
    assert c_comment_safe(name) == name


@pytest.mark.parametrize(
    "name", ["a*/b", "x\\", "", "Temp", "a b", "temp\n", "t\u00e9mp", "a/b", "a-b", "\u0131"]
)
def test_c_comment_safe_rejects(name):
    with pytest.raises(ValueError):
        c_comment_safe(name)
