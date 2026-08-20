"""Repository gate behavior."""

import check


def test_non_lf_tracked_finds_only_text_with_crlf_or_mixed_endings() -> None:
    report = (
        "i/lf    w/lf    attr/text eol=lf\tclean.py\n"
        "i/lf    w/crlf  attr/text eol=lf\twindows.py\n"
        "i/lf    w/mixed attr/text eol=lf\tmixed.json\n"
        "i/-text w/-text attr/text eol=lf\tfont.woff2\n"
    )

    assert check.non_lf_tracked(report) == ["windows.py", "mixed.json"]
