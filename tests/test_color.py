"""Contract tests for present/color.py (CL1–CL3, clicolors spec)."""

import pytest

from pyprobe.present.color import (
    BOLD,
    CYAN,
    DIM,
    GREEN,
    RED,
    RESET,
    YELLOW,
    cyan,
    dim,
    green,
    red,
    should_color,
    yellow_bold,
)

HELPERS = [yellow_bold, green, cyan, dim, red]


class FakeStream:
    def __init__(self, tty):
        self._tty = tty

    def isatty(self):
        return self._tty


class TestHelpersIdentity:
    """CL1：color=False（默认）恒等返回——非着色路径与无颜色输出逐字节
    一致（重定向/管道的 byte 稳定性承诺）。"""

    @pytest.mark.parametrize("helper", HELPERS)
    def test_identity(self, helper):
        assert helper("text") == "text"
        assert helper("text", False) == "text"


class TestHelpersWrap:
    """CL2：五色语义点位（PID/TID 黄粗、函数绿、文件青、行号/PC 暗、错误红）。"""

    def test_yellow_bold(self):
        assert yellow_bold("42", True) == f"{BOLD}{YELLOW}42{RESET}"

    def test_green(self):
        assert green("fn", True) == f"{GREEN}fn{RESET}"

    def test_cyan(self):
        assert cyan("f.py", True) == f"{CYAN}f.py{RESET}"

    def test_dim(self):
        assert dim("10", True) == f"{DIM}10{RESET}"

    def test_red(self):
        assert red("[!] err", True) == f"{RED}[!] err{RESET}"

    def test_empty_string_wrapped_asis(self):
        """CL2 边界：空串照常包装（调用方无需特判）。"""
        assert green("", True) == f"{GREEN}{RESET}"


class TestShouldColor:
    """CL3：clicolors 优先级链（FORCE > NO_COLOR > tty > TERM=dumb > CLICOLOR）。"""

    @pytest.fixture(autouse=True)
    def clean_env(self, monkeypatch):
        for var in ("CLICOLOR_FORCE", "NO_COLOR", "TERM", "CLICOLOR"):
            monkeypatch.delenv(var, raising=False)

    def test_not_a_tty(self):
        """CL3：非 tty 基线 → False。"""
        assert should_color(FakeStream(False)) is False

    def test_tty_baseline(self):
        """CL3：tty 基线 → True。"""
        assert should_color(FakeStream(True)) is True

    def test_no_color_disables(self, monkeypatch):
        """CL3：NO_COLOR 非空 → False。"""
        monkeypatch.setenv("NO_COLOR", "1")
        assert should_color(FakeStream(True)) is False

    def test_empty_no_color_does_not_disable(self, monkeypatch):
        """CL3：NO_COLOR 空串不失效（spec 细节，防误判）。"""
        monkeypatch.setenv("NO_COLOR", "")
        assert should_color(FakeStream(True)) is True

    def test_dumb_term_disables(self, monkeypatch):
        """CL3：TERM=dumb → False。"""
        monkeypatch.setenv("TERM", "dumb")
        assert should_color(FakeStream(True)) is False

    def test_clicolor_zero_disables(self, monkeypatch):
        """CL3：CLICOLOR=0 → False。"""
        monkeypatch.setenv("CLICOLOR", "0")
        assert should_color(FakeStream(True)) is False

    def test_clicolor_one_explicit(self, monkeypatch):
        """CL3：CLICOLOR=1 显式 → True（与缺省一致）。"""
        monkeypatch.setenv("CLICOLOR", "1")
        assert should_color(FakeStream(True)) is True

    def test_force_overrides_non_tty(self, monkeypatch):
        """CL3：CLICOLOR_FORCE 压非 tty（强制输出的唯一通道）。"""
        monkeypatch.setenv("CLICOLOR_FORCE", "1")
        assert should_color(FakeStream(False)) is True

    def test_force_beats_no_color(self, monkeypatch):
        """CL3：CLICOLOR_FORCE 优先级高于 NO_COLOR（spec 明确）。"""
        monkeypatch.setenv("CLICOLOR_FORCE", "1")
        monkeypatch.setenv("NO_COLOR", "1")
        assert should_color(FakeStream(True)) is True

    def test_force_zero_is_inert(self, monkeypatch):
        """CL3：CLICOLOR_FORCE=0 等于未设置（"0" 是唯一惰性值）。"""
        monkeypatch.setenv("CLICOLOR_FORCE", "0")
        assert should_color(FakeStream(True)) is True
