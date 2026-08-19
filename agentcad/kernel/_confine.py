"""In-process Linux confinement: Landlock (filesystem) + seccomp-BPF (syscalls).

Pure ``ctypes`` — no external binary, no capability, no daemon. This is the
half of PRD-006 that runs **inside the worker**, applied by
:mod:`agentcad.kernel._preamble` before ``import build123d``, because that is
the only place where the process doing the confining and the process running
arbitrary part-script Python are the same process (design spec, Decision 1:
``bwrap`` needs ``unshare``, which Docker's default seccomp profile denies).

Three mechanisms, applied in this order by the preamble:

1. :func:`apply_rlimits` — POSIX, used on macOS as well as Linux. Soft equals
   hard, so a script cannot raise a cap back after the preamble lowered it.
2. :func:`landlock_apply` — reads and writes. The handled-access mask is built
   from the **probed** ABI, never hard-coded, for two reasons: a bit the
   running kernel does not know makes ``create_ruleset`` ``EINVAL`` and takes
   the whole ruleset with it, and where the ABI *does* have
   ``LANDLOCK_ACCESS_FS_TRUNCATE`` (bit 14, ABI 3) every write root has to be
   granted it too — ``open(path, "w")`` sets ``O_TRUNC``, and a write root
   without that right is ``EACCES`` on every truncating open (the spike
   measured exactly that on ``/proc/self/clear_refs``).
   :data:`LANDLOCK_MIN_ABI` is the floor the design sets.
3. :func:`seccomp_apply` — the network and signal filter of Decisions 1 and 11.

Nothing here runs at import time: the module is imported on macOS and Windows
too (the preamble is one code path for all three) and every entry point is
callable — and honest — on a platform that has none of this.
"""

from __future__ import annotations

import ctypes
import errno
import functools
import os
import platform
import struct
import sys

try:                              # POSIX only; Windows has no `resource`
    import resource as _resource
except ImportError:               # pragma: no cover - exercised on Windows CI
    _resource = None

#: ``platform.machine()`` -> the syscall numbers and ``AUDIT_ARCH_*`` constant
#: for that ABI. A machine that is not in here reports confinement
#: ``unsupported`` rather than guessing: a filter written against the wrong
#: table would deny (or allow) whatever syscall happens to share the number.
ARCH: dict[str, dict[str, int]] = {
    "x86_64": {"audit": 0xC000003E, "socket": 41, "socketpair": 53, "kill": 62,
               "tkill": 200, "tgkill": 234, "rt_sigqueueinfo": 129,
               "rt_tgsigqueueinfo": 297, "ptrace": 101,
               "process_vm_readv": 310, "process_vm_writev": 311,
               "pidfd_open": 434, "pidfd_send_signal": 424,
               "pidfd_getfd": 438, "process_madvise": 440, "seccomp": 317,
               "io_uring_setup": 425, "io_uring_enter": 426,
               "io_uring_register": 427,
               "landlock_create_ruleset": 444, "landlock_add_rule": 445,
               "landlock_restrict_self": 446},
    "aarch64": {"audit": 0xC00000B7, "socket": 198, "socketpair": 199,
                "kill": 129, "tkill": 130, "tgkill": 131,
                "rt_sigqueueinfo": 138, "rt_tgsigqueueinfo": 240,
                "ptrace": 117, "process_vm_readv": 270,
                "process_vm_writev": 271, "pidfd_open": 434,
                "pidfd_send_signal": 424, "pidfd_getfd": 438,
                "process_madvise": 440, "seccomp": 277,
                "io_uring_setup": 425, "io_uring_enter": 426,
                "io_uring_register": 427,
                "landlock_create_ruleset": 444, "landlock_add_rule": 445,
                "landlock_restrict_self": 446},
}

#: Landlock ABI version -> the filesystem access bits that version handles.
#: ABI 4 adds network rights (not filesystem), ABI 6 adds scoping, so the mask
#: repeats. Passing a bit the running kernel does not know is ``EINVAL`` on
#: ``landlock_create_ruleset``, which would take the whole ruleset with it.
LANDLOCK_ABI_MASK = {1: 0x1FFF, 2: 0x3FFF, 3: 0x7FFF, 4: 0x7FFF, 5: 0xFFFF,
                     6: 0xFFFF}

#: EXECUTE | READ_FILE | READ_DIR — everything a read root needs and nothing
#: that writes.
FS_READ = 0b1000 | 0b100 | 0b1

#: Bit 14, ABI 3+. A write root granted every *other* bit but not this one is
#: EACCES on `open(path, "w")` — which is most writes a part script makes.
FS_TRUNCATE = 1 << 14

#: Bit 15, ABI 5+.
FS_IOCTL_DEV = 1 << 15

#: The rights Landlock accepts on a rule whose path is a **file** rather than a
#: directory (kernel ``ACCESS_FILE``). Adding a directory-only right to a file
#: rule is ``EINVAL``, which is how ``/dev/null`` and ``/proc/self/clear_refs``
#: fail if they are granted the full mask.
FS_FILE = 0b111 | FS_TRUNCATE | FS_IOCTL_DEV

#: The floor the design sets (kernel 6.2). Below it the write-root model still
#: applies, but ``TRUNCATE`` is not a right the kernel knows, so the ruleset
#: cannot express the full write semantics a part script needs — and shipping
#: a half-model quietly is exactly what Decision 8 forbids. The preamble
#: therefore applies **no** ruleset there and the client reports ``off`` with
#: the ABI in the reason.
LANDLOCK_MIN_ABI = 3

LANDLOCK_CREATE_RULESET_VERSION = 1 << 0
LANDLOCK_RULE_PATH_BENEATH = 1

PR_SET_NO_NEW_PRIVS = 38
PR_SET_SECCOMP = 22
SECCOMP_MODE_FILTER = 2          # prctl(PR_SET_SECCOMP, 2, &prog)
SECCOMP_SET_MODE_FILTER = 1      # seccomp(2)'s *operation* — 2 is EOPNOTSUPP
SECCOMP_FILTER_FLAG_TSYNC = 1

# BPF, as much of linux/filter.h as the filter uses.
BPF_LD_W_ABS = 0x20
BPF_JEQ_K = 0x15
BPF_JGE_K = 0x35
BPF_JA = 0x05
BPF_RET_K = 0x06

SECCOMP_RET_KILL_PROCESS = 0x80000000
SECCOMP_RET_ERRNO = 0x00050000
SECCOMP_RET_ALLOW = 0x7FFF0000

#: ``struct seccomp_data``: u32 nr, u32 arch, u64 instruction_pointer,
#: u64 args[6]. Little-endian, so a 64-bit argument's low word comes first.
OFF_NR, OFF_ARCH, OFF_ARG0_LOW = 0, 4, 16

#: An ``int`` argument is only the **low 32 bits** of the register, and that is
#: what the kernel truncates it to (``SYSCALL_DEFINE2(kill, pid_t, ...)``). A
#: filter that tested the high word for a sign-extended negative would miss
#: every negative pid on arm64, where ``mov w0, #-1`` zeroes the top half —
#: measured: ``os.kill(-1, SIGKILL)`` escaped such a filter in the shipped
#: image and returned ``ESRCH`` instead of ``EPERM``. Unsigned ``JGE`` against
#: this constant is "the int is negative".
INT_SIGN_BIT = 0x80000000

AF_UNIX = 1

#: x32 syscalls carry this bit; the numbers past it are a different table.
X32_SYSCALL_BIT = 0x40000000

_SIGNAL_SYSCALLS = ("kill", "tkill", "tgkill", "rt_sigqueueinfo",
                    "rt_tgsigqueueinfo")

#: Denied outright, no argument inspection. ``ptrace``/``process_vm_*``/
#: ``pidfd_open`` are reaching into another process; **io_uring** is here for a
#: different and sharper reason: it is a *submission queue*, so a script can
#: ask the kernel to open a socket (or anything else) from a ring entry, and
#: the syscall the filter would see is ``io_uring_enter``, not ``socket``. A
#: seccomp filter cannot inspect ring entries at all, so the whole interface
#: has to go or the socket rule above is decorative. Nothing in CPython, numpy,
#: OCP or build123d uses it.
#:
#: The **pidfd family** is here for the same reason as io_uring, and it is the
#: sharper hole of the two: ``pidfd_send_signal(pidfd, sig, ...)`` names its
#: target by a *file descriptor*, so the argument the signal rules inspect
#: (``args[0]``, a ``pid_t``) is not a pid at all and the whole
#: negative-pid/server-pid analysis below never applies. Denying ``pidfd_open``
#: alone is not enough: **a ``/proc/<pid>`` directory fd is a valid pidfd**, and
#: ``/proc`` is readable in both postures — so a part script could
#: ``os.open("/proc/<server pid>", O_RDONLY|O_DIRECTORY)`` and SIGKILL the
#: server through it (verified live in the shipped image). ``pidfd_getfd``
#: steals another process's descriptors and ``process_madvise`` reaches into
#: another process's address space through the same handle. Nothing in CPython,
#: OCP or build123d calls any of the three (``os.pidfd_open`` is the only
#: exposed one, and it is already denied).
_PEEK_SYSCALLS = ("ptrace", "process_vm_readv", "process_vm_writev",
                  "pidfd_open", "pidfd_send_signal", "pidfd_getfd",
                  "process_madvise", "io_uring_setup", "io_uring_enter",
                  "io_uring_register")


# ------------------------------------------------------------------ plumbing

#: `syscall(2)` takes at most six arguments after the number, so one fixed
#: signature covers every call this module makes. Binding it **once** matters:
#: `landlock_abi()` is called from the server, which is threaded, and mutating
#: `argtypes` on a cached, shared function object per call is a data race.
#: Unused trailing arguments are passed as zeros — harmless on x86_64 and
#: aarch64, where syscall arguments are registers the kernel simply ignores.
_SYSCALL_ARITY = 6


@functools.lru_cache(maxsize=1)
def _libc():
    """libc with ``syscall``/``prctl`` fully typed, or ``None`` where there is
    none. Both signatures are bound here and never touched again."""
    try:
        lib = ctypes.CDLL(None, use_errno=True)
        lib.syscall.restype = ctypes.c_long
        lib.syscall.argtypes = [ctypes.c_long] * (1 + _SYSCALL_ARITY)
        lib.prctl.restype = ctypes.c_int
        lib.prctl.argtypes = [ctypes.c_int] + [ctypes.c_ulong] * 4
        return lib
    except (OSError, AttributeError):  # pragma: no cover - Windows
        return None


def _syscall(number: int, *args: int) -> tuple[int, int]:
    """``syscall(number, ...)`` -> ``(result, errno)``. Linux only."""
    lib = _libc()
    if lib is None or sys.platform != "linux":
        return -1, errno.ENOSYS
    if len(args) > _SYSCALL_ARITY:
        raise ValueError(f"syscall takes at most {_SYSCALL_ARITY} arguments")
    padded = list(args) + [0] * (_SYSCALL_ARITY - len(args))
    ctypes.set_errno(0)
    result = lib.syscall(number, *padded)
    return result, ctypes.get_errno()


def _prctl(option: int, *args: int) -> tuple[int, int]:
    lib = _libc()
    if lib is None or sys.platform != "linux":
        return -1, errno.ENOSYS
    padded = list(args) + [0] * (4 - len(args))
    ctypes.set_errno(0)
    return lib.prctl(option, *padded), ctypes.get_errno()


def _errno_name(number: int) -> str:
    return errno.errorcode.get(number, f"errno {number}")


def _arch_table() -> dict[str, int] | None:
    """This machine's syscall numbers, or ``None`` if it has no table."""
    return ARCH.get(platform.machine())


# ------------------------------------------------------------------ rlimits

def known_rlimits(names) -> list[str]:
    """The subset of *names* this platform actually has a limit for.

    The payload is written by the server, which may not be the worker's OS in
    a test, and ``RLIMIT_NPROC`` genuinely does not exist everywhere — so the
    preamble needs to tell "absent here" (fine) from "present and refused"
    (a cap that is not in force) before it reports a failure.
    """
    if _resource is None:
        return []
    return [name for name in names if hasattr(_resource, name)]


def apply_rlimits(rlimits: dict[str, list[int]]) -> list[str]:
    """Apply ``{"RLIMIT_AS": [soft, hard], ...}``; return the names applied.

    A name this platform does not have is **skipped**, not an error: the
    payload is written by the server, which may be a different OS than the
    reader in a cross-platform test, and ``RLIMIT_NPROC`` genuinely does not
    exist everywhere. A name that exists but that the kernel refuses (Darwin
    answers ``EINVAL`` for ``RLIMIT_AS``) is also skipped — and its absence
    from the returned list is what the preamble reports, so nothing ever
    claims a cap that is not in force.
    """
    if _resource is None:
        return []
    applied: list[str] = []
    for name in sorted(rlimits):
        which = getattr(_resource, name, None)
        if which is None:
            continue
        try:
            soft, hard = (int(v) for v in rlimits[name])
        except (TypeError, ValueError):
            continue
        try:
            _resource.setrlimit(which, (soft, hard))
        except (OSError, ValueError):
            continue
        applied.append(name)
    return applied


# ----------------------------------------------------------------- landlock

def landlock_abi() -> int:
    """The kernel's Landlock ABI version, or ``0`` when there is none.

    ``0`` covers every "no": not Linux, ``ENOSYS`` (kernel < 5.13), and
    ``EOPNOTSUPP`` (Landlock compiled in but missing from the boot-time ``lsm=``
    list — the case that makes a new kernel report nothing at all).
    """
    table = _arch_table()
    if table is None or sys.platform != "linux":
        return 0
    version, _err = _syscall(table["landlock_create_ruleset"], 0, 0,
                             LANDLOCK_CREATE_RULESET_VERSION)
    return version if version > 0 else 0


def landlock_apply(read_roots, write_roots, extra_files) -> dict:
    """Restrict this process to the given roots.

    Returns ``{"abi", "applied", "rules", "failed"}`` — plus ``"reason"`` when
    it did not apply. *failed* holds ``(path, errno_name)`` for every rule that
    could not be added; a **missing path is not fatal** (a write root the
    client has not created yet, ``/lib64`` on a machine that has no such
    directory), it is recorded and the rest of the ruleset still lands.

    Raises ``OSError`` **only** if ``landlock_restrict_self`` fails — that is
    the one failure where the process believes it is confined and is not.
    """
    abi = landlock_abi()
    table = _arch_table()
    if table is None:
        return {"abi": abi, "applied": False, "rules": 0, "failed": [],
                "reason": f"unsupported machine {platform.machine()!r}"}
    if abi < LANDLOCK_MIN_ABI:
        return {"abi": abi, "applied": False, "rules": 0, "failed": [],
                "reason": (f"landlock ABI {abi} < {LANDLOCK_MIN_ABI} "
                           f"(no TRUNCATE right: every truncating open would "
                           f"be denied)")}

    # Clamped, not looked up: a kernel newer than this table still gets the
    # widest mask we know is valid, rather than a KeyError.
    handled = LANDLOCK_ABI_MASK[min(abi, max(LANDLOCK_ABI_MASK))]
    attr = ctypes.create_string_buffer(struct.pack("=Q", handled), 8)
    # Attr size 8 (`handled_access_fs` alone) is accepted by every ABI; the
    # wider structs of ABI 4/6 are EINVAL on an older kernel.
    ruleset_fd, err = _syscall(table["landlock_create_ruleset"],
                               ctypes.addressof(attr), 8, 0)
    if ruleset_fd < 0:
        return {"abi": abi, "applied": False, "rules": 0, "failed": [],
                "reason": f"landlock_create_ruleset: {_errno_name(err)}"}

    failed: list[tuple[str, str]] = []
    rules = 0
    try:
        def add(path: str, access: int) -> None:
            nonlocal rules
            access &= handled
            if not access:
                return
            try:
                path_fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
            except OSError as exc:
                failed.append((path, _errno_name(exc.errno)))
                return
            try:
                rule = ctypes.create_string_buffer(
                    struct.pack("=QI", access, path_fd), 12)
                result, rule_err = _syscall(
                    table["landlock_add_rule"], ruleset_fd,
                    LANDLOCK_RULE_PATH_BENEATH, ctypes.addressof(rule), 0)
                if result == 0:
                    rules += 1
                else:
                    failed.append((path, _errno_name(rule_err)))
            finally:
                os.close(path_fd)

        for path in read_roots:
            add(path, FS_READ)
        for path in write_roots:
            add(path, handled)
        for path in extra_files:
            # A rule on a file may only carry file rights — a directory-only
            # bit makes the whole add EINVAL, which is how `/dev/null` and
            # `/proc/self/clear_refs` fail when granted the full mask.
            add(path, FS_FILE)

        # Landlock needs no_new_privs; if that fails, restrict_self fails
        # below and raises, which is the honest place for it.
        no_new_privs, np_err = _prctl(PR_SET_NO_NEW_PRIVS, 1)
        if no_new_privs != 0:
            failed.append(("PR_SET_NO_NEW_PRIVS", _errno_name(np_err)))
        result, restrict_err = _syscall(table["landlock_restrict_self"],
                                        ruleset_fd, 0)
        if result != 0:
            raise OSError(restrict_err,
                          f"landlock_restrict_self: {_errno_name(restrict_err)}")
    finally:
        os.close(ruleset_fd)
    return {"abi": abi, "applied": True, "rules": rules, "failed": failed}


# ------------------------------------------------------------------ seccomp

def _insn(code: int, jt: int, jf: int, k: int) -> bytes:
    assert 0 <= jt < 256 and 0 <= jf < 256, "BPF jump offsets are 8 bits"
    return struct.pack("=HBBI", code, jt, jf, k & 0xFFFFFFFF)


def seccomp_program(arch: str, server_pid: int) -> bytes:
    """The BPF filter, as the bytes ``seccomp(2)`` takes.

    Built (and unit-tested) separately from :func:`seccomp_apply` because a
    wrong jump offset here is a silently permissive sandbox and the dev box
    cannot install the filter to find out. Default is ``ALLOW``: this is a
    scalpel for network and cross-process reach, not an allow-list of the
    ~300 syscalls CPython and OCCT make.
    """
    table = ARCH.get(arch)
    if table is None:
        raise ValueError(
            f"no seccomp syscall table for {arch!r}; known: "
            f"{', '.join(sorted(ARCH))}")

    # Laid out as labelled blocks and resolved at the end, so adding a rule
    # never means recounting offsets by hand. `"next"` is the pseudo-label for
    # "fall through to the following instruction" — the common case in a
    # dispatch chain, and the one that would otherwise need a fresh label per
    # comparison.
    program: list[tuple] = []          # (code, jt_label|None, jf_label|None, k)
    labels: dict[str, int] = {}

    def emit(code, k=0, jt=None, jf=None) -> None:
        program.append((code, jt, jf, k))

    def label(name: str) -> None:
        labels[name] = len(program)

    emit(BPF_LD_W_ABS, OFF_ARCH)
    emit(BPF_JEQ_K, table["audit"], jt="arch_ok", jf="kill")
    label("arch_ok")
    emit(BPF_LD_W_ABS, OFF_NR)
    emit(BPF_JGE_K, X32_SYSCALL_BIT, jt="kill", jf="dispatch")
    label("kill")
    emit(BPF_RET_K, SECCOMP_RET_KILL_PROCESS)

    label("dispatch")
    for name in ("socket", "socketpair"):
        emit(BPF_JEQ_K, table[name], jt="socket_domain", jf="next")
    for name in _SIGNAL_SYSCALLS:
        emit(BPF_JEQ_K, table[name], jt="signal_target", jf="next")
    for name in _PEEK_SYSCALLS:
        emit(BPF_JEQ_K, table[name], jt="deny", jf="next")
    emit(BPF_JA, 0, jt="allow")

    # socket(domain, ...) / socketpair(domain, ...): AF_UNIX and nothing else.
    # `socketpair()` is what multiprocessing needs; every other family is a
    # network reach, and the worker's own protocol is a plain pipe.
    label("socket_domain")
    emit(BPF_LD_W_ABS, OFF_ARG0_LOW)
    emit(BPF_JEQ_K, AF_UNIX, jt="allow", jf="deny")

    # kill/tkill/tgkill/rt_sigqueueinfo/rt_tgsigqueueinfo: a negative pid is a
    # broadcast or a process group (`kill(-1, 9)` takes the whole uid down,
    # server included), 0 is this process's own group, and server_pid is the
    # server. Signals at self and at a script's own children stay allowed.
    #
    # Every signal syscall that names its target by a *pid* is here; the ones
    # that name it by a **descriptor** (`pidfd_send_signal`) cannot be filtered
    # this way at all and are denied outright above — see `_PEEK_SYSCALLS`.
    label("signal_target")
    emit(BPF_LD_W_ABS, OFF_ARG0_LOW)
    emit(BPF_JGE_K, INT_SIGN_BIT, jt="deny", jf="next")   # a negative pid_t
    emit(BPF_JEQ_K, 0, jt="deny", jf="next")
    emit(BPF_JEQ_K, server_pid & 0xFFFFFFFF, jt="deny", jf="allow")

    label("deny")
    emit(BPF_RET_K, SECCOMP_RET_ERRNO | (errno.EPERM & 0xFFFF))
    label("allow")
    emit(BPF_RET_K, SECCOMP_RET_ALLOW)

    out = bytearray()

    def target(name: str, index: int) -> int:
        if name == "next":
            return index + 1
        return labels[name]

    for index, (code, jt, jf, k) in enumerate(program):
        if code in (BPF_JEQ_K, BPF_JGE_K):
            out += _insn(code, target(jt, index) - index - 1,
                         target(jf, index) - index - 1, k)
        elif code == BPF_JA:
            out += _insn(code, 0, 0, target(jt, index) - index - 1)
        else:
            out += _insn(code, 0, 0, k)
    return bytes(out)


def seccomp_apply(server_pid: int) -> str:
    """Install the filter on this process; return how it was installed.

    ``"seccomp(2)"`` covers every thread (``TSYNC``) and is preferred;
    ``"prctl"`` is the fallback and filters only the calling thread — which is
    still the whole worker at preamble time, since nothing has started a
    thread yet. Raises ``OSError`` if neither lands: a caller that believes it
    filtered and did not is exactly what Decision 8 forbids.
    """
    table = _arch_table()
    if table is None:
        raise OSError(errno.ENOTSUP,
                      f"no seccomp syscall table for {platform.machine()!r}")
    program = seccomp_program(platform.machine(), server_pid)
    filter_buf = ctypes.create_string_buffer(program, len(program))
    fprog = ctypes.create_string_buffer(
        struct.pack("=HxxxxxxQ", len(program) // 8,
                    ctypes.addressof(filter_buf)), 16)

    result, err = _prctl(PR_SET_NO_NEW_PRIVS, 1)
    if result != 0:
        raise OSError(err, f"PR_SET_NO_NEW_PRIVS: {_errno_name(err)}")
    result, err = _syscall(table["seccomp"], SECCOMP_SET_MODE_FILTER,
                           SECCOMP_FILTER_FLAG_TSYNC, ctypes.addressof(fprog))
    if result == 0:
        return "seccomp(2)"
    fallback, fallback_err = _prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER,
                                    ctypes.addressof(fprog))
    if fallback != 0:
        raise OSError(fallback_err,
                      f"seccomp: seccomp(2) {_errno_name(err)}, "
                      f"prctl {_errno_name(fallback_err)}")
    return "prctl"
