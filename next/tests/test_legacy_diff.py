"""Batch-7 alignment gate: old vs new CLI on the SAME parented target (§10.5-1).

主代理 oracle 资产（§10.7）：归一化规则与对比脚本在此，先入 tests 接受 diff
审查。**替换（§10.6）时删除本套件与 legacy_diff_runner.py**——旧树消失后对比
退化为自比，无意义；模块级 skip 保证万一残留也只跳过不误报。

运行模型：本测试**不**在 pytest 进程内切换双树（那会清空 sys.modules，破坏
后续测试模块在导入期绑定的异常类身份，实测令 `pytest.raises(PyProbeError)`
漏捕）。改由 `legacy_diff_runner.py` 子进程派生目标（它是目标的祖先 → yama
ptrace_scope=1 下允许），在同一进程内依次以旧树/新树导入 pyprobe 调用
cli.main，输出 JSON 回传。同一目标故 pid/tid/thread_id/pc/渲染内地址天然一致，
只需归一化计时字段。
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

NEXT_ROOT = Path(__file__).resolve().parent.parent        # .../next
REPO_ROOT = NEXT_ROOT.parent                              # repo root (legacy)
TARGETS = NEXT_ROOT / "tests" / "targets"
RUNNER = NEXT_ROOT / "tests" / "legacy_diff_runner.py"

if not (REPO_ROOT / "pyprobe" / "cli.py").exists():
    pytest.skip("legacy tree absent (post-swap); the diff gate is batch-7 only",
                allow_module_level=True)

# --- normalization (documented timing-only deltas, §10.5-1) -----------------

_TEXT_ELAPSED = re.compile(r"<\d+\.\d+>")
_JSON_ELAPSED = re.compile(r'"elapsed": [0-9.eE+-]+')
_SUMMARY_FLOAT = re.compile(r"\d+\.\d{6}")


def _norm(text):
    text = _TEXT_ELAPSED.sub("<ELAPSED>", text)
    text = _JSON_ELAPSED.sub('"elapsed": E', text)
    return _SUMMARY_FLOAT.sub("N", text)


def run_both(script, *argv, timeout=30):
    """Invoke the dual-tree runner; return the parsed JSON payload.

    Payload keys: pid, old, new (each [rc, stdout, stderr]), and
    old_output/new_output (folded file contents when -o is used).
    """
    env = dict(os.environ)
    env.pop("CLICOLOR_FORCE", None)
    proc = subprocess.run(
        [sys.executable, str(RUNNER), str(REPO_ROOT), str(NEXT_ROOT),
         str(TARGETS / script), *argv],
        capture_output=True, text=True, timeout=timeout, env=env)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def assert_same(script, *argv):
    payload = run_both(script, *argv)
    old, new = payload["old"], payload["new"]
    assert old[0] == new[0], f"rc differs: {old[0]} vs {new[0]}"
    assert _norm(old[1]) == _norm(new[1]), _diff(_norm(old[1]), _norm(new[1]))
    assert _norm(old[2]) == _norm(new[2]), _diff(_norm(old[2]), _norm(new[2]))
    return old, new


def _diff(a, b):
    import difflib
    return "\n".join(difflib.unified_diff(
        a.splitlines(), b.splitlines(), "old", "new", lineterm=""))


# --- CLI diff cases (§10.5-1) -----------------------------------------------

class TestStackDiff:
    """stack --json / --native --json 是强制验证项（ADR A2 native 行为有变）。"""

    def test_stack_text(self):
        """文本输出对固定 fixture 逐字节一致（路径缩短/线程升序/idle 标记）。"""
        assert_same("target_app.py", "stack", "-p", "__PID__", "--color", "never")

    def test_stack_verbose(self):
        """-v 全路径文本一致。"""
        assert_same("target_app.py", "stack", "-p", "__PID__", "-v",
                    "--color", "never")

    def test_stack_json(self):
        """stack --json schema 与值一致（pid/native_tid 同一目标故天然相等）。"""
        assert_same("target_app.py", "stack", "-p", "__PID__", "--json")

    def test_stack_native_text(self):
        """native 文本一致（A2 ATTACH→SEIZE 后帧/符号/模块头格式未变）。"""
        assert_same("target_app.py", "stack", "-p", "__PID__", "--native",
                    "--color", "never")

    def test_stack_native_json(self):
        """**强制验证项**：stack --native --json 逐字节一致（ADR A2）。"""
        assert_same("target_app.py", "stack", "-p", "__PID__", "--native", "--json")

    def test_stack_native_verbose(self):
        """native -v 全模块路径一致。"""
        assert_same("target_app.py", "stack", "-p", "__PID__", "--native", "-v",
                    "--color", "never")


class TestSyscallDiff:
    """syscall --json 是门禁项；用确定性 getuid 目标消除事件流/参数抖动。"""

    def test_syscall_text(self):
        """逐事件文本一致（SY9 复刻约束：六参数全显、渲染规则）。"""
        assert_same("syscall_diff_app.py", "syscall", "-p", "__PID__",
                    "-e", "trace=getuid", "--max-events", "3", "--color", "never")

    def test_syscall_json(self):
        """syscall --json 逐字节一致（仅 elapsed 归一）。"""
        old, new = assert_same(
            "syscall_diff_app.py", "syscall", "-p", "__PID__",
            "-e", "trace=getuid", "--max-events", "3", "--json")
        assert [e["name"] for e in json.loads(old[1])["events"]] == \
            [e["name"] for e in json.loads(new[1])["events"]]

    def test_syscall_summary(self):
        """--summary 表格形态一致（仅计时列归一；TX7 复刻约束 total/s 列）。"""
        assert_same("syscall_diff_app.py", "syscall", "-p", "__PID__",
                    "-e", "trace=getuid", "--max-events", "3", "--summary",
                    "--color", "never")


class TestRecordDiff:
    def test_record_folded_key_set(self, tmp_path):
        """record folded 比键集合（计数天然抖动，§10.5-1）。spin_app 保证
        有 R 态 burn 帧（sleep-only 目标全 idle，folded 必空）。runner 在
        每棵树运行后各读一次 -o 文件，故旧/新内容均在 payload 中。"""
        out = tmp_path / "out.folded"
        payload = run_both(
            "spin_app.py", "record", "-p", "__PID__", "-r", "100", "-d", "0.6",
            "-o", str(out), "--color", "never")
        assert payload["old"][0] == payload["new"][0] == 0
        old_keys = {ln.rsplit(" ", 1)[0]
                    for ln in (payload["old_output"] or "").splitlines()}
        new_keys = {ln.rsplit(" ", 1)[0]
                    for ln in (payload["new_output"] or "").splitlines()}
        assert old_keys == new_keys
        assert any("burn" in k for k in new_keys)


class TestErrorAndMetaDiff:
    """错误路径与元命令：rc + stderr 逐字节一致。"""

    def test_version_flag(self):
        """--version 前缀一致（版本号因装包而定，只比前缀）。"""
        p = run_both("target_app.py", "--version")
        old, new = p["old"], p["new"]
        assert old[0] == new[0] == 0
        assert old[1].startswith("pyprobe ") and new[1].startswith("pyprobe ")

    def test_missing_pid(self):
        """缺 -p → argparse rc=2，两树一致（CLI2）。"""
        for argv in (["stack"], ["syscall"], ["record"], ["top"]):
            p = run_both("target_app.py", *argv)
            old, new = p["old"], p["new"]
            assert old[0] == new[0] == 2
            assert _norm(old[2]) == _norm(new[2])

    def test_unknown_subcommand(self):
        """未知子命令 → rc=2。"""
        p = run_both("target_app.py", "frobnicate")
        assert p["old"][0] == p["new"][0] == 2

    def test_bad_rate(self):
        """越界 rate → rc=2（CLI2 消息形态）。"""
        p = run_both("target_app.py", "record", "-p", "__PID__", "-r", "0")
        assert p["old"][0] == p["new"][0] == 2
        assert _norm(p["old"][2]) == _norm(p["new"][2])

    def test_dead_pid(self):
        """不存在的 pid：两树 rc=1 + `[!]` stderr 形态一致。"""
        p = run_both("target_app.py", "stack", "-p", str(1 << 22),
                     "--color", "never")
        assert p["old"][0] == p["new"][0] == 1
        assert "[!]" in p["old"][2] and "[!]" in p["new"][2]


# --- §10.5-2 library API surface ---------------------------------------------

class TestApiSurfaceDiff:
    def test_all_list_matches_legacy(self):
        """API1（§10.5-2）：新 `__all__` == 旧树 `__all__` + A1 例外
        resolve_layout。旧名单经子进程自旧树读取（不靠测试内的抄本）。"""
        legacy_all = _load_all(REPO_ROOT)
        new_all = _load_all(NEXT_ROOT)
        assert set(new_all) == set(legacy_all) | {"resolve_layout"}
        assert "resolve_layout" in new_all


def _load_all(tree_root):
    code = (
        "import json, sys\n"
        "for f in list(sys.meta_path):\n"
        "    m = getattr(f, '__module__', '') or ''\n"
        "    if 'editable' in m.lower() or 'editable' in type(f).__name__.lower():\n"
        "        sys.meta_path.remove(f)\n"
        "import pyprobe; print(json.dumps(pyprobe.__all__))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, cwd=str(tree_root))
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)
