"""Naming what a confinement or a quota refused, from the exception it raised.

A breach that happens *inside* a part script stays what it already is — a
`script_error` with a traceback, a line number and an Error Doctor hint
(design spec, Decision 9). This module adds the one word that tells an agent
whether to fix the script or shrink the job: ``details.denied`` ∈
``{network, filesystem, process_count, memory}``.

String-level on purpose. The worker calls it with the formatted exception, so
the same four answers cover a Landlock EACCES and a seccomp EPERM on Linux, a
macOS seatbelt EACCES/EPERM (declared by the parent that wrapped the argv —
see :func:`active_facets`), and a job object's `MemoryError` on Windows
without this module knowing which one is in force.

Whether an answer may be *given* is per-facet (:func:`active_facets`), because
"something is confining this process" is not evidence for all four: a worker
with an applied fork budget and no Landlock can attest ``process_count`` and
nothing about the filesystem.

Deliberately importable from server code: no ``OCP``/build123d, no OS call.
"""

from __future__ import annotations

#: EAGAIN is 11 on Linux and 35 on macOS — the fork cap answers with whichever
#: the platform uses, and both mean "the process budget is spent".
_EAGAIN = ("[Errno 11]", "[Errno 35]", "Resource temporarily unavailable")

#: EPERM alone does not mean "network": the seccomp filter answers EPERM for a
#: refused `kill` too, and the seatbelt for several operations. So the frames
#: have to show a socket call. `connect` also covers `create_connection` and
#: `connection`, which is how `socket.create_connection` matches.
_NETWORK_FRAMES = ("socket", "urlopen", "connect", "getaddrinfo")

#: ``WSAEACCES``: what Winsock answers when the process's token carries no
#: network capability — an AppContainer without ``INTERNET_CLIENT``, which is
#: how the Windows confinement denies the network (PRD-006b, Decision 2).
#: CPython renders it as ``PermissionError: [WinError 10013] An attempt was
#: made to access a socket in a way forbidden by its access permissions``, with
#: **no** ``[Errno 13]`` and no "Permission denied" in it, so none of the rules
#: below sees it and an unconfined-looking `None` was the answer.
_WSAEACCES = "WinError 10013"


def _names_a_path(message: str) -> bool:
    """Whether *message* carries the quoted filename ``OSError.__str__``
    appends when the call that raised it named a path.

    CPython renders a filename-bearing ``OSError`` as ``"[Errno N] strerror:
    'path'"`` and a filename-free one (``os.kill``, a raw ``socket.socket()``)
    as bare ``"[Errno N] strerror"``. That single ``": '"`` is real evidence
    of a file operation — the seatbelt's own ``deny file-write*`` answers with
    EPERM, not EACCES, so without this an EPERM from a genuine macOS
    filesystem denial stayed unattributed forever.
    """
    return ": '" in message


#: The four answers, and what a worker must have **actually applied** before it
#: may give each one. Read as a claim, which is what makes it per-facet
#: (review M3): a worker with a fork budget and no Landlock has real evidence
#: for ``process_count`` and none at all for ``filesystem``, and one blanket
#: "something is in force" let it label an ordinary DAC ``EACCES`` a sandbox
#: denial — sending the reader to look for a cap that is not there.
FACETS = ("filesystem", "network", "process_count", "memory")


def active_facets(report) -> frozenset[str]:
    """Which of :data:`FACETS` this worker's preamble report can support.

    *report* is ``_preamble.REPORT`` — what the worker applied to **itself**,
    never what the client intended:

    * ``filesystem`` needs a Landlock ABI. That is the only filesystem
      confinement a worker can attest from inside; with ``AGENTCAD_NO_SANDBOX``
      the ABI is ``None`` and an ``EACCES`` is an ordinary permissions bug.
    * ``network`` needs the seccomp filter — the thing that refuses the socket.
    * ``process_count`` and ``memory`` need an applied rlimit **or** a
      ``quotas`` entry: a tier the *parent* installed around this process (a
      Windows job object, a cgroup's ``pids.max``) whose breach still surfaces
      in here as a plain ``MemoryError``/``BlockingIOError``.
    * ``filesystem``/``network`` are ALSO active when the *parent* declared
      them in ``report["confinement"]`` **and** the worker did not contradict
      it: on Windows the same declaration accompanies an intended AppContainer,
      and a lowbox spawn that failed leaves a perfectly ordinary worker holding
      a payload that says it is confined, so an ``appcontainer`` key that is
      not ``True`` drops the declaration entirely. The macOS case has no such
      key: the seatbelt wraps the argv before the worker ever runs, so there is
      no ``landlock_abi`` or ``seccomp`` for it to self-report, but the
      confinement is genuinely in force and the parent
      (``sandbox_macos.build``) knows it. This is a declared fact from the
      process that actually applied the wrap, never the worker guessing at its
      own environment.

    A consequence worth stating out loud: on **macOS** the seatbelt is applied
    to the argv by the parent, so a seatbelt ``EACCES``/``EPERM`` is labelled
    from the parent's declaration rather than the worker's own Landlock/seccomp
    report. The error, its traceback and the Error Doctor's
    ``sandbox_write_denied`` / ``sandbox_network_denied`` hints (which match on
    the message) are unchanged either way.
    """
    if not isinstance(report, dict):
        return frozenset()
    facets: set[str] = set()
    if report.get("landlock_abi"):
        facets.add("filesystem")
    if report.get("seccomp"):
        facets.add("network")
    if report.get("rlimits") or report.get("quotas"):
        facets.update(("process_count", "memory"))
    declared = report.get("confinement") or ()
    if "appcontainer" in report and report.get("appcontainer") is not True:
        # Windows, and the mirror of `client.confinement_holds`: the parent
        # declares the two facets when it *intends* to spawn into an
        # AppContainer, but the lowbox spawn can fail and fall back to a plain
        # `Popen` — and then an ordinary `[Errno 13]` from a DAC permission bug
        # would be labelled a sandbox denial by a worker that is not confined
        # at all. The key exists only where the worker looked at its own token,
        # so the macOS seatbelt path (which has no such key) is untouched, and
        # `is not True` covers the token check that failed as well as the one
        # that said no.
        declared = ()
    if "filesystem" in declared:
        facets.add("filesystem")
    if "network" in declared:
        facets.add("network")
    return frozenset(facets)


def _claimable(active, facet: str) -> bool:
    """Whether *facet* may be named, for a bool or a collection of facets."""
    if isinstance(active, bool):
        return active
    return facet in (active or ())


def classify(exc_type: str, message: str, *, active, traceback: str = "") -> str | None:
    """The denial *exc_type*/*message* represents, or ``None``.

    *active* is what this worker actually applied (the preamble's own report —
    never an intention), either as a plain ``bool`` or, better, as the
    per-facet set :func:`active_facets` computes. With nothing applied the
    answer is always ``None``: an unconfined worker's ``PermissionError`` is a
    plain file-permission bug in the script, and calling it a sandbox denial
    would send the reader looking for a cap that does not exist.

    *traceback* is the formatted traceback, and it is what separates a network
    denial from every other EPERM. Without it an EPERM is only classified when
    the message itself names the call — an unattributed one stays ``None``,
    because a wrong label sends an agent to fix the wrong thing (a script whose
    `os.kill` the filter refused is not a script with a networking bug).

    EPERM is also, on macOS, what the seatbelt's own ``deny file-write*``
    answers with — unlike Landlock's EACCES for the same refusal — so a bare
    "errno 1 is never filesystem" rule mislabelled every real seatbelt write
    denial. The tell is the message itself: CPython's ``OSError.__str__``
    appends the failing path only when the call that raised it named one
    (``open()``, ``os.rename()`` — never ``os.kill()`` or a raw
    ``socket.socket()``), so a path in the message is exactly as much evidence
    of a file operation as a socket frame is of a network one.
    """
    if exc_type == "MemoryError":
        return "memory" if _claimable(active, "memory") else None
    if exc_type == "PermissionError":
        if _WSAEACCES in message:
            # Winsock's own refusal, and the socket-frame rule still applies:
            # `WSAEACCES` is also what a bind to a reserved port answers, so
            # the evidence has to be a socket call — which, for this one, the
            # message itself carries ("access a socket in a way forbidden").
            frames = f"{message}\n{traceback}"
            if any(marker in frames for marker in _NETWORK_FRAMES):
                return "network" if _claimable(active, "network") else None
            return None
        # EPERM is the kernel refusing the *operation*; EACCES is it refusing
        # the *path*. Only the second one is unambiguous on its own.
        if "[Errno 1]" in message or "Operation not permitted" in message:
            frames = f"{message}\n{traceback}"
            if any(marker in frames for marker in _NETWORK_FRAMES):
                return "network" if _claimable(active, "network") else None
            if _names_a_path(message):
                return "filesystem" if _claimable(active, "filesystem") else None
            # Deliberately NOT falling through any further: an EPERM naming
            # neither a socket frame nor a path is some other refused
            # operation (a signal, ptrace, a sysctl write), and an
            # unattributed EPERM is no denial at all.
            return None
        if "[Errno 13]" in message or "Permission denied" in message:
            return "filesystem" if _claimable(active, "filesystem") else None
        return None
    if exc_type in ("BlockingIOError", "OSError"):
        if any(marker in message for marker in _EAGAIN):
            return "process_count" if _claimable(active, "process_count") else None
    return None
