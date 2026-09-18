"""Shared pytest fixtures for pyprobe tests."""

import os
import subprocess
import sys
import time

import pytest

from pyprobe import offsets


@pytest.fixture(autouse=True)
def _reset_offsets():
    """Ensure each test starts with a clean, configured offsets table."""
    offsets.configure("3.12")
    yield


def _can_read_descendant():
    """Quick check whether process_vm_readv works on a descendant.

    Returns True on Linux; the actual capability is verified at runtime by
    the integration tests themselves, which skip on failure.
    """
    return sys.platform == "linux"


@pytest.fixture(scope="session")
def target_pid():
    """Spawn a child target process and yield its PID.

    The child is a descendant of the pytest process, so ``process_vm_readv``
    works under the default ``ptrace_scope=1``.  The child is terminated when
    the session ends.
    """
    if not _can_read_descendant():
        pytest.skip("integration tests require Linux process_vm_readv")

    # TARGET_PYTHON selects the interpreter of the *target* process (pyprobe
    # itself keeps running on the venv interpreter); used to verify other
    # CPython versions, e.g. TARGET_PYTHON=/usr/bin/python3.13
    interpreter = os.environ.get("TARGET_PYTHON") or sys.executable
    target_script = os.path.join(os.path.dirname(__file__), "targets", "target_app.py")
    child = subprocess.Popen(
        [interpreter, target_script],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        line = child.stdout.readline()
        if not line:
            pytest.skip("target process failed to start")
        pid = int(line.split(":")[-1].strip())
        time.sleep(0.5)  # let threads spin up
        yield pid
    finally:
        child.terminate()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
