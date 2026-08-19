"""PRD-006 slice 2 — `kernel/_confine.py`, the parts that need no Linux.

The Landlock ruleset and the seccomp filter can only be *applied* on Linux
(`tests/test_sandbox_linux.py` drives the real thing inside the shipped
image), but the filter itself is a hand-assembled BPF program, and a wrong
jump offset there is a silently permissive sandbox. So the program is built
and **interpreted** here, on every OS: a 20-line BPF machine runs the real
bytes against synthetic `seccomp_data` and asserts the verdict for each rule
the design promises.

Everything in this file is pure: no syscall is made except by the rlimit
probe, which runs in a subprocess so it cannot lower this process's limits.
"""

from __future__ import annotations

import json
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from agentcad.kernel import _confine

# seccomp filter verdicts (linux/seccomp.h)
KILL_PROCESS = 0x80000000
ERRNO_EPERM = 0x00050001
ALLOW = 0x7FFF0000

AUDIT_X86_64 = 0xC000003E
AUDIT_AARCH64 = 0xC00000B7

AF_UNIX, AF_INET, AF_INET6 = 1, 2, 10

SERVER_PID = 4242


def _decode(program: bytes) -> list[tuple[int, int, int, int]]:
    assert len(program) % 8 == 0, "sock_filter is 8 bytes"
    return list(struct.iter_unpack("=HBBI", program))


def _run(program: bytes, *, nr: int, arch: int, arg0: int = 0) -> int:
    """Interpret the filter against one synthetic ``seccomp_data``.

    Layout: ``u32 nr; u32 arch; u64 instruction_pointer; u64 args[6]``. Only
    the four opcodes the generator emits are implemented — anything else is a
    test failure, which is itself the assertion that nothing exotic crept in.
    """
    data = struct.pack("=IIQ", nr, arch, 0) + struct.pack("=Q", arg0) + b"\0" * 40
    insns = _decode(program)
    pc, acc, steps = 0, 0, 0
    while True:
        steps += 1
        assert steps < 1000, "the filter does not terminate"
        assert 0 <= pc < len(insns), f"jumped out of the program at {pc}"
        code, jt, jf, k = insns[pc]
        pc += 1
        if code == 0x20:          # BPF_LD | BPF_W | BPF_ABS
            acc = struct.unpack_from("=I", data, k)[0]
        elif code == 0x15:        # BPF_JMP | BPF_JEQ | BPF_K
            pc += jt if acc == k else jf
        elif code == 0x35:        # BPF_JMP | BPF_JGE | BPF_K
            pc += jt if acc >= k else jf
        elif code == 0x05:        # BPF_JMP | BPF_JA
            pc += k
        elif code == 0x06:        # BPF_RET | BPF_K
            return k
        else:
            raise AssertionError(f"unexpected opcode {code:#x} at {pc - 1}")


# --------------------------------------------------------------- the program

def test_the_program_starts_by_checking_the_architecture():
    """A filter written for one arch is nonsense on another (the syscall
    numbers move), so the arch check is instruction 0 and its failure is a
    kill, not an errno."""
    insns = _decode(_confine.seccomp_program("x86_64", SERVER_PID))
    assert insns[0] == (0x20, 0, 0, 4)            # LD W ABS seccomp_data.arch
    # One shared kill block, reached from the arch check and the x32 check —
    # `test_a_foreign_arch_or_an_x32_call_kills_the_process` proves both.
    assert [i for i in insns if i[0] == 0x06 and i[3] == KILL_PROCESS]
    assert (0x06, 0, 0, ERRNO_EPERM) in insns
    assert (0x06, 0, 0, ALLOW) in insns


def test_the_program_names_the_arch_the_pid_and_af_unix():
    insns = _decode(_confine.seccomp_program("x86_64", SERVER_PID))
    constants = [k for _code, _jt, _jf, k in insns]
    assert AUDIT_X86_64 in constants
    assert 41 in constants and 53 in constants    # socket, socketpair
    assert SERVER_PID in constants
    assert any(code == 0x15 and k == AF_UNIX for code, _jt, _jf, k in insns)

    aarch64 = _decode(_confine.seccomp_program("aarch64", SERVER_PID))
    constants = [k for _code, _jt, _jf, k in aarch64]
    assert AUDIT_AARCH64 in constants
    assert 198 in constants and 199 in constants  # socket, socketpair


def test_every_jump_lands_inside_the_program():
    """The one bug hand-assembled BPF really has: an offset computed against
    the wrong base silently skips a deny branch."""
    for arch in ("x86_64", "aarch64"):
        insns = _decode(_confine.seccomp_program(arch, SERVER_PID))
        for index, (code, jt, jf, k) in enumerate(insns):
            if code in (0x15, 0x35):
                assert index + 1 + jt < len(insns), (arch, index, "jt")
                assert index + 1 + jf < len(insns), (arch, index, "jf")
            elif code == 0x05:
                assert index + 1 + k < len(insns), (arch, index, "ja")


def test_an_unknown_architecture_is_refused_rather_than_guessed():
    with pytest.raises(ValueError, match="riscv64"):
        _confine.seccomp_program("riscv64", SERVER_PID)


# ------------------------------------------------------------ what it denies

@pytest.mark.parametrize("arch,audit", [("x86_64", AUDIT_X86_64),
                                        ("aarch64", AUDIT_AARCH64)])
def test_only_af_unix_sockets_are_allowed(arch, audit):
    program = _confine.seccomp_program(arch, SERVER_PID)
    numbers = _confine.ARCH[arch]
    for name in ("socket", "socketpair"):
        nr = numbers[name]
        assert _run(program, nr=nr, arch=audit, arg0=AF_UNIX) == ALLOW
        for domain in (AF_INET, AF_INET6, 16, 40):  # inet, inet6, netlink, ...
            assert _run(program, nr=nr, arch=audit, arg0=domain) == ERRNO_EPERM


@pytest.mark.parametrize("name", ["kill", "tkill", "tgkill",
                                  "rt_sigqueueinfo", "rt_tgsigqueueinfo"])
def test_signals_at_the_server_and_at_everyone_are_denied(name):
    """`kill(-1, 9)` would take the whole uid down, the server with it; a
    signal at `server_pid` is the same attack aimed. A script may still
    signal itself and its own children."""
    program = _confine.seccomp_program("x86_64", SERVER_PID)
    nr = _confine.ARCH["x86_64"][name]
    # `pid_t` is an int, and a negative one reaches `seccomp_data.args[0]`
    # BOTH ways: sign-extended (x86_64 glibc) and zero-extended (arm64, where
    # `mov w0, #-1` clears the top half). The filter tests the low word,
    # because that is what the kernel truncates the argument to — a filter
    # that tested the high word let `kill(-1, SIGKILL)` through on arm64,
    # measured in the shipped image.
    assert _run(program, nr=nr, arch=AUDIT_X86_64,
                arg0=(1 << 64) - 1) == ERRNO_EPERM          # -1, sign-extended
    assert _run(program, nr=nr, arch=AUDIT_X86_64,
                arg0=0xFFFFFFFF) == ERRNO_EPERM             # -1, zero-extended
    assert _run(program, nr=nr, arch=AUDIT_X86_64,
                arg0=(1 << 64) - 4242) == ERRNO_EPERM       # a process group
    assert _run(program, nr=nr, arch=AUDIT_X86_64, arg0=0) == ERRNO_EPERM
    assert _run(program, nr=nr, arch=AUDIT_X86_64, arg0=SERVER_PID) == ERRNO_EPERM
    assert _run(program, nr=nr, arch=AUDIT_X86_64, arg0=99_999) == ALLOW


@pytest.mark.parametrize("name", ["ptrace", "process_vm_readv",
                                  "process_vm_writev", "pidfd_open"])
def test_reaching_into_another_process_is_denied_outright(name):
    program = _confine.seccomp_program("x86_64", SERVER_PID)
    nr = _confine.ARCH["x86_64"][name]
    assert _run(program, nr=nr, arch=AUDIT_X86_64, arg0=1) == ERRNO_EPERM


@pytest.mark.parametrize("arch,audit", [("x86_64", AUDIT_X86_64),
                                        ("aarch64", AUDIT_AARCH64)])
@pytest.mark.parametrize("name,number", [("pidfd_send_signal", 424),
                                         ("pidfd_getfd", 438),
                                         ("process_madvise", 440)])
def test_the_pidfd_family_is_denied_because_it_names_targets_by_fd(
        arch, audit, name, number):
    """The hole the pid-argument signal rules leave (review C1).

    `pidfd_send_signal(pidfd, sig, ...)` names its target by a **file
    descriptor**, so `args[0]` is not a `pid_t` and the negative-pid /
    server-pid analysis never runs on it. Denying `pidfd_open` is not enough:
    a `/proc/<pid>` directory fd is a valid pidfd and `/proc` is readable in
    both postures, so a script could open `/proc/<server pid>` and SIGKILL the
    server through it — verified live in the shipped image before this rule.
    `pidfd_getfd` (steal a descriptor) and `process_madvise` (reach into
    another address space) travel the same handle. The numbers are the same on
    x86_64 and aarch64.
    """
    assert _confine.ARCH[arch][name] == number
    program = _confine.seccomp_program(arch, SERVER_PID)
    # Every argument shape: an fd, fd 0, and a large one. None of them is a pid.
    for arg0 in (0, 3, 99_999):
        assert _run(program, nr=number, arch=audit, arg0=arg0) == ERRNO_EPERM


@pytest.mark.parametrize("arch,audit", [("x86_64", AUDIT_X86_64),
                                        ("aarch64", AUDIT_AARCH64)])
@pytest.mark.parametrize("name,number", [("io_uring_setup", 425),
                                         ("io_uring_enter", 426),
                                         ("io_uring_register", 427)])
def test_io_uring_is_denied_because_the_socket_rule_cannot_see_through_it(
        arch, audit, name, number):
    """The hole a socket-only filter leaves: io_uring is a submission queue, so
    a script can ask the kernel to open and use a socket from a ring entry and
    the only syscall the filter ever sees is `io_uring_enter`. seccomp cannot
    inspect ring entries, so the interface has to be denied outright or the
    AF_UNIX rule above is decorative. The numbers are the same on both arches.
    """
    assert _confine.ARCH[arch][name] == number
    program = _confine.seccomp_program(arch, SERVER_PID)
    assert _run(program, nr=number, arch=audit, arg0=8) == ERRNO_EPERM


def test_a_foreign_arch_or_an_x32_call_kills_the_process():
    program = _confine.seccomp_program("x86_64", SERVER_PID)
    assert _run(program, nr=1, arch=AUDIT_AARCH64) == KILL_PROCESS
    assert _run(program, nr=0x40000000 | 41, arch=AUDIT_X86_64,
                arg0=AF_INET) == KILL_PROCESS


def test_everything_else_is_allowed():
    """The filter is a scalpel: openat, mmap, write and clone all pass, or a
    confined worker could not import OCCT, let alone build."""
    program = _confine.seccomp_program("x86_64", SERVER_PID)
    for nr in (0, 1, 2, 9, 56, 57, 59, 257, 435):
        assert _run(program, nr=nr, arch=AUDIT_X86_64, arg0=0) == ALLOW


# ------------------------------------------------------------ the ABI masks

def test_truncate_arrives_with_abi_three():
    """`open(path, "w")` sets O_TRUNC, which needs
    `LANDLOCK_ACCESS_FS_TRUNCATE` on the rule — the spike's false denial, and
    the reason a write root is granted the whole handled mask. Below ABI 3 the
    right does not exist at all, which is why ABI 3 is the declared floor."""
    assert _confine.LANDLOCK_ABI_MASK[3] & _confine.FS_TRUNCATE
    assert _confine.LANDLOCK_ABI_MASK[2] & _confine.FS_TRUNCATE == 0
    assert _confine.LANDLOCK_MIN_ABI == 3
    # The read grant is EXECUTE|READ_FILE|READ_DIR and nothing else.
    assert _confine.FS_READ == 0xD
    assert _confine.FS_READ & _confine.LANDLOCK_ABI_MASK[1] == _confine.FS_READ


# -------------------------------------------------------------- the rlimits

_RLIMIT_PROBE = """
import json
import resource
from agentcad.kernel._confine import apply_rlimits

applied = apply_rlimits({"RLIMIT_NOFILE": [512, 512],
                         "RLIMIT_NO_SUCH_THING": [1, 1]})
soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
print(json.dumps({"applied": applied, "soft": soft, "hard": hard}))
"""


@pytest.mark.integration
@pytest.mark.portability
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX rlimits")
def test_apply_rlimits_sets_soft_and_hard_and_skips_what_the_os_lacks():
    """In a subprocess: `apply_rlimits` lowers a real limit irreversibly, so
    running it in-process would cap the test session's own file descriptors."""
    repo = Path(__file__).resolve().parents[1]
    proc = subprocess.run([sys.executable, "-c", _RLIMIT_PROBE], cwd=repo,
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout)
    assert report["applied"] == ["RLIMIT_NOFILE"]   # the unknown name is skipped
    assert report["soft"] == 512
    assert report["hard"] == 512                    # hard == soft: no raising it back


@pytest.mark.skipif(sys.platform != "win32", reason="the Windows branch")
def test_apply_rlimits_is_empty_where_there_is_no_resource_module():
    assert _confine.apply_rlimits({"RLIMIT_NOFILE": [512, 512]}) == []


# ------------------------------------------------- callable, quietly, anywhere

def test_the_landlock_probe_answers_zero_off_linux():
    """`_confine` is imported by the preamble on every OS (the payload decides
    what runs), so the probes must be callable — and honest — off Linux."""
    abi = _confine.landlock_abi()
    assert isinstance(abi, int)
    if sys.platform != "linux":
        assert abi == 0
        report = _confine.landlock_apply(["/"], [], [])
        assert report["applied"] is False
        assert report["abi"] == 0
        assert "reason" in report
    else:
        assert abi >= 0
