"""Unit tests for pyprobe.colors — ANSI helpers and clicolors detection.

``should_color`` follows the clicolors spec (https://bixense.com/clicolors/):
CLICOLOR_FORCE forces color on unconditionally; otherwise color requires a
tty without NO_COLOR, without TERM=dumb, and CLICOLOR != "0".
"""

import pytest

from pyprobe import colors


class FakeStream:
    """Minimal file-like stand-in whose isatty() is injected."""

    def __init__(self, tty):
        self._tty = tty

    def isatty(self):
        return self._tty


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Normalize the environment so the matrix below is deterministic.

    CI runners may export NO_COLOR or TERM=dumb, which would otherwise
    poison every case.
    """
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("CLICOLOR", raising=False)
    monkeypatch.delenv("CLICOLOR_FORCE", raising=False)
    monkeypatch.setenv("TERM", "xterm")


class TestHelpersIdentity:
    """I1: with color=False every helper returns its input unchanged."""

    @pytest.mark.parametrize("fn", [
        colors.yellow_bold, colors.green, colors.cyan, colors.dim, colors.red,
    ])
    def test_identity(self, fn):
        assert fn("some text") == "some text"


class TestHelpersWrap:
    def test_yellow_bold(self):
        assert colors.yellow_bold("1", True) == "\x1b[1m\x1b[33m1\x1b[0m"

    def test_green(self):
        assert colors.green("x", True) == "\x1b[32mx\x1b[0m"

    def test_cyan(self):
        assert colors.cyan("x", True) == "\x1b[36mx\x1b[0m"

    def test_dim(self):
        assert colors.dim("10", True) == "\x1b[2m10\x1b[0m"

    def test_red(self):
        assert colors.red("[!] err", True) == "\x1b[31m[!] err\x1b[0m"

    def test_empty_string_is_wrapped_asis(self):
        assert colors.green("", True) == "\x1b[32m\x1b[0m"


class TestShouldColor:
    def test_not_a_tty(self):
        assert colors.should_color(FakeStream(tty=False)) is False

    def test_tty_baseline(self):
        assert colors.should_color(FakeStream(tty=True)) is True

    def test_no_color_disables(self, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "1")
        assert colors.should_color(FakeStream(tty=True)) is False

    def test_empty_no_color_does_not_disable(self, monkeypatch):
        # no-color.org: only a non-empty value disables.
        monkeypatch.setenv("NO_COLOR", "")
        assert colors.should_color(FakeStream(tty=True)) is True

    def test_dumb_term_disables(self, monkeypatch):
        monkeypatch.setenv("TERM", "dumb")
        assert colors.should_color(FakeStream(tty=True)) is False

    def test_clicolor_zero_disables(self, monkeypatch):
        monkeypatch.setenv("CLICOLOR", "0")
        assert colors.should_color(FakeStream(tty=True)) is False

    def test_clicolor_one_explicit(self, monkeypatch):
        monkeypatch.setenv("CLICOLOR", "1")
        assert colors.should_color(FakeStream(tty=True)) is True

    def test_force_overrides_non_tty(self, monkeypatch):
        monkeypatch.setenv("CLICOLOR_FORCE", "1")
        assert colors.should_color(FakeStream(tty=False)) is True

    def test_force_beats_no_color(self, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "1")
        monkeypatch.setenv("CLICOLOR_FORCE", "1")
        assert colors.should_color(FakeStream(tty=True)) is True

    def test_force_zero_is_inert(self, monkeypatch):
        monkeypatch.setenv("CLICOLOR_FORCE", "0")
        assert colors.should_color(FakeStream(tty=True)) is True
        assert colors.should_color(FakeStream(tty=False)) is False
