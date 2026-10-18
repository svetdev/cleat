"""assertions — how many tests a file holds, how many checks they make, and which make none.

A test that runs code and checks nothing passes whatever the code does; it is
what an agent asked for coverage writes. Read statically, per language: the
file is cut at each test's start — `def test_`, `it(`/`test(`, `func test…`,
`@Test`, `#[test]`, `func TestX(t *testing.T)`, `it "…"` — and each piece up to
the next start is that test. A piece with no assertion in it is a test that
checks nothing. An assertion is the runner's own (`assert`, `XCTAssert…`,
`#expect`, `assert_eq!`, `t.Errorf`, testify's `assert.`/`require.`, node's
`assert.equal`, tap's `t.equal`) or any
call named for one — assert…, expect…, verify…, should…, must…, refute… — so a
project's helper built on those names (`expectEqual`, `assert_raises_message`)
counts. A helper named otherwise (`check(…)`, a builder's `.run()`) does not,
and a suite built on one reads as checking less than it does — the reference
sample has the same blind spot.
"""

import re

TEST_START = {
    "python": r"^\s*(?:async\s+)?def\s+test\w*\s*\(",
    "typescript": r"(?<![\w.])(?:it|test)(?:\.only|\.skip|\.concurrent|\.each\s*\([^)]*\))?\s*\(\s*['\"`]",
    "swift": r"^\s*(?:@Test\b|(?:@\w+\s+)*func\s+test\w*\s*\()",
    "rust": r"#\[(?:tokio::|async_std::)?test\b",
    "go": r"^func\s+Test\w*\s*\(\s*\w+\s+\*testing\.T\s*\)",
    "kotlin": r"@Test\b",
    "java": r"@Test\b",
    "ruby": r"^\s*(?:it|specify|scenario)\b\s*(?:\(|['\"]|do\b|\{)|^\s*def\s+test_\w+",
}
# An assertion is a call named for one — assert…, expect…, verify…, should…, must…, refute… —
# which takes in a project's own helpers built on those names (`expectEqual`, `assert_raises_message`,
# `expect_offense`), plus each runner's own spelling. Rust's `.expect(` unwraps and is not one.
_NAMED = r"\b(?:assert|expect|verify|should|must|refute)\w*\s*[!(]"
ASSERTION = {
    "python": _NAMED + r"|\bassert\b|\bself\.assert\w+|\bself\.fail\s*\(|\bpytest\.(?:raises|fail|warns)\b|\b(?:eq|ne|is|is_not|in|not_in)_\s*\(",
    "typescript": _NAMED + r"|\.should\b|\bexpectTypeOf\b|\bassert\.\w+\s*\("
                  r"|\bt\.(?:assert\.\w+|equal|not\w*|ok|same|strictSame|deepEqual|match|throws|rejects|is|true|false|plan)\s*\(",
    "swift": _NAMED + r"|\bXCTAssert\w*\s*\(|\bXCTFail\s*\(|\bXCTUnwrap\s*\(|#expect\s*\(|#require\s*\(",
    "rust": r"\b(?:debug_)?assert\w*!\s*\(|\bassert\w*\s*\(|#\[should_panic|\bpanic!\s*\(|\.unwrap_err\s*\(|\bprop_assert\w*!\s*\(",
    "go": _NAMED + r"|\bt\.(?:Error|Errorf|Fatal|Fatalf|Fail|FailNow)\s*\(|\b(?:assert|require)\.\w+\s*\(|\bqt\.Assert|\bc\.Assert\s*\(",
    "kotlin": _NAMED + r"|\bassert\w*\s*\{|\bverify\w*\s*\{|\bshould\w*\b|\bfail\s*\(",
    "java": _NAMED + r"|\bfail\s*\(",
    "ruby": r"\b(?:assert|expect|refute|must|wont|is_expected)\w*|\.should\b",
}
_START = {k: re.compile(v, re.MULTILINE) for k, v in TEST_START.items()}
_ASSERT = {k: re.compile(v) for k, v in ASSERTION.items()}


def knows(language):
    return language in _START


def scan_text(text, language):
    """(tests, assertions, tests with no assertion) in one file's text."""
    start, check = _START.get(language), _ASSERT.get(language)
    if start is None:
        return 0, 0, 0
    starts = [m.start() for m in start.finditer(text)]
    if not starts:
        return 0, 0, 0
    pieces = [text[a:b] for a, b in zip(starts, starts[1:] + [len(text)])]
    counts = [len(check.findall(piece)) for piece in pieces]
    return len(pieces), sum(counts), sum(1 for c in counts if c == 0)


def scan(path, language):
    try:
        with open(path, errors="replace") as handle:
            return scan_text(handle.read(), language)
    except OSError:
        return 0, 0, 0
