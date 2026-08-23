"""Content-addressed part and assembly thumbnails (PRD-027 FR4).

Three claims hold this module together, and every function here exists to keep
one of them true:

1. **A thumbnail is derived data keyed by the build's cache key.** It lives at
   ``.cache/<cache_key>.thumb.png`` beside the mesh it was rendered from. The
   key already hashes script + params + density + tolerance, so a script edit
   refreshes the thumbnail *by construction* — there is no invalidation path to
   get wrong, and two parts that build to one key share one file. ``.thumb.png``
   is in ``project._TRIMMABLE``, so the janitor sweeps a trimmed key's thumb
   with its mesh.
2. **Nothing here ever reaches the kernel.** Not ``ensure_mesh``, not
   ``_ensure_built``, not ``get_assembly``. A thumbnail is rendered from a mesh
   that is *already on disk* or it is not rendered at all — which is what makes
   a dashboard of twenty projects a directory walk rather than twenty builds.
   It does not even call ``service._resolved_instances``, which is a seam
   `tools_structure` rebinds onto `mates.resolve_project`; `_instances` below
   says exactly which transforms that costs us and why it is the right trade.
3. **It is never on the rebuild path.** :class:`ThumbnailWarmer` is a
   *pre-warm*: a daemon thread that reacts to ``rebuild_finished`` after the
   fact. Every route renders on demand anyway (≈5–20 ms at 192²), so a service
   with the warmer off answers identically, only colder.

The rasterizer is ``core/render.py`` — numpy + stdlib PNG, no OCP — so this
module is importable in the server process, which is where both the routes and
the warmer thread run.
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import struct
import sys
import threading
from collections import OrderedDict
from pathlib import Path

from ..kernel import acm
from . import render
from .model import AppError
from .project import ProjectStore

try:
    # The same optional seam `service._resolved_instances` treats it as: only
    # the two PURE helpers `_unit`/`_member` are used from it, never `resolve`,
    # `expand` or `resolve_project` (every one of those reaches the kernel).
    from . import mates
except ImportError:  # pragma: no cover — mates ships with every build we make
    mates = None

#: Thumbnails are square and small on purpose: the tree row and the dashboard
#: card both draw them at ≤ 96 CSS px, so 192 covers a 2× display exactly and
#: the LOD1 tier is visually indistinguishable from the full mesh at this size.
THUMB_SIZE = 192
THUMB_VIEW = "iso"

#: An assembly composite is keyed by a hash of its instances, not by a build,
#: so it needs a filename that cannot collide with a part's. The separator is a
#: **dash, not a dot**: `trim_cache` buckets a cache entry by
#: ``name.split(".", 1)[0]``, so ``asm.<hash>.thumb.png`` would bucket as the
#: bare string ``asm`` — one key shared by every assembly thumbnail ever
#: written. ``asm-<hash>.thumb.png`` gets its own bucket, which is also why it
#: is safe that the composite key is *not* in `_referenced_cache_keys`: the
#: janitor may sweep it when it is old and the cache is over the watermark, and
#: the next read re-renders it.
ASM_PREFIX = "asm-"

#: How many parts the assembly fallback will examine before giving up. Each
#: candidate costs a script read + hash (`_cache_key_for`), and this runs on a
#: dashboard listing, so it is bounded rather than proportional to the project.
_FALLBACK_SCAN = 64


# ------------------------------------------------------------------- paths

def thumb_path(cache_dir: Path, key: str) -> Path:
    """Where the thumbnail for one cache key lives."""
    return Path(cache_dir) / f"{key}.thumb.png"


def mesh_for_key(cache_dir: Path, key: str) -> Path | None:
    """The cheapest mesh to rasterize for *key*, or None if nothing is built.

    The LOD1 sidecar wins when it exists: the worker only writes one for a part
    over ``LOD_TRIANGLE_THRESHOLD``, which is exactly the part whose full mesh
    is expensive to rasterize — and at 192² the difference is invisible. A
    small part has no tier, and falling back to the full mesh is the normal
    case, not an error.
    """
    cache_dir = Path(cache_dir)
    tier = cache_dir / f"{key}.lod1.acm"
    if tier.is_file():
        return tier
    full = cache_dir / f"{key}.acm"
    return full if full.is_file() else None


# ------------------------------------------------------------------ render

def _read_mesh(path: Path) -> dict | None:
    """`acm.read`, but a corrupt or half-written buffer is "no mesh".

    These functions run on a **route**, and the mesh they open is derived data
    a janitor may be deleting underneath them: a truncated, empty or
    just-trimmed `.acm` must be a 404 (or a warmer skip), never a 500. The
    parser signals that three ways — `struct.error` from the fixed header,
    `ValueError` for a bad magic or a short body out of `np.frombuffer`, and
    `OSError` if the file vanished between the `is_file()` and the read.
    """
    try:
        return acm.read(path)
    except (OSError, ValueError, struct.error):
        return None


def _has_triangles(meshes: list[dict]) -> bool:
    return any(len(m["indices"]) for m in meshes)


def _render(meshes: list[dict]) -> bytes:
    return render.render_acm(meshes, view=THUMB_VIEW,
                             width=THUMB_SIZE, height=THUMB_SIZE)


def _too_many_triangles(meshes: list[dict]) -> bool:
    # Read through the module, never `from .render import MAX_TRIANGLES`: the
    # limit is a knob a test (and a future config) moves, and a bound name
    # would freeze it at import time.
    total = sum(len(m["indices"]) for m in meshes)
    return total > render.MAX_TRIANGLES


def render_part_thumb(cache_dir: Path, key: str) -> bytes | None:
    """Render and cache the thumbnail for one mesh cache key.

    Returns the PNG bytes, or **None** when there is no mesh on disk for *key*,
    when what is there cannot be parsed (`_read_mesh`), when the mesh has no
    triangles, or when it exceeds ``render.MAX_TRIANGLES`` even after the LOD1
    preference. The write is atomic (``_atomic_write``
    stages through a random name), so a concurrent renderer of the same key
    loses rather than corrupts — and both wrote the same bytes anyway.
    """
    cache_dir = Path(cache_dir)
    mesh_file = mesh_for_key(cache_dir, key)
    if mesh_file is None:
        return None
    mesh = _read_mesh(mesh_file)
    if mesh is None:
        return None
    meshes = [{"positions": mesh["positions"], "normals": mesh["normals"],
               "indices": mesh["indices"], "transform": None, "color": None}]
    if not _has_triangles(meshes) or _too_many_triangles(meshes):
        return None
    png = _render(meshes)
    ProjectStore._atomic_write(thumb_path(cache_dir, key), png)
    return png


# -------------------------------------------------------------- part thumbs

def part_key(service, proj: str, part_id: str) -> str:
    """The cache key a thumbnail of this part must be addressed by.

    ``_status`` first, because that is what ``get_project`` publishes as
    ``thumb_key`` and therefore what the browser puts in ``?k=`` — the two must
    agree or the immutable answer would never fire. When the service has no
    memory of the part (a fresh process, whose ``_status`` is empty, is the
    normal case for a dashboard) the key is **recomputed purely** from the
    record: `_cache_key_for` reads the script, the params and the density and
    hashes them, exactly as a build would, and reaches nothing else. An unknown
    part raises `NotFoundError` from `_record_for`, which is the 404 the route
    wants.
    """
    status = service._status.get(service._status_key(proj, part_id))
    if status is not None and status.get("state") == "ok" and status.get("cache_key"):
        return status["cache_key"]
    return service._cache_key_for(proj, service._record_for(proj, part_id))


def part_thumb(service, proj: str, part_id: str) -> tuple[bytes, str] | None:
    """``(png, key)`` for one part, or None when it has no mesh to render.

    Never builds: an unbuilt part, or one whose only build failed, has no mesh
    on disk and is simply None (the route's 404). A cached thumbnail is served
    from disk; otherwise it is rendered synchronously and cached — the warmer
    is a pre-warm, never a dependency.
    """
    # Recorded, deferred: this always resolves the CURRENT key (a script read
    # and a hash) even when the caller named one in `?k=` whose PNG is sitting
    # in the cache; a fast path that serves `<k>.thumb.png` without re-hashing
    # would trade that hash for a weaker freshness guarantee.
    key = part_key(service, proj, part_id)
    cache = service.store.cache_dir(proj)
    path = thumb_path(cache, key)
    if path.is_file():
        try:
            return path.read_bytes(), key
        except OSError:
            pass   # racing with the janitor: fall through and re-render
    png = render_part_thumb(cache, key)
    return (png, key) if png is not None else None


# ---------------------------------------------------------------- assembly

def _instances(service, proj: str):
    """The instances to composite, **without ever reaching the kernel**.

    This deliberately does NOT call ``service._resolved_instances``. That
    attribute is a *rebound seam*: `tools_structure._install_expansion`
    replaces it with `mates.resolve_project`, which fires on ``mate or pattern
    or assembly`` and whose `mates.expand` issues one
    ``kernel.request("resolve_assembly", …)`` for every **polar** pattern
    member and every **sub-assembly** member, plus a ``resolve_mates`` round
    trip (which *executes every part's script*) for the mate pass. Guarding on
    ``mate`` alone was not enough — an unmated polar pattern reached the kernel
    from ``GET /thumb.png``. So this walks ``store.instances`` itself, which is
    a manifest read and nothing else, and re-implements only the part of the
    expansion that is provably pure:

    * **linear pattern** → expanded here, with `mates`' own `_unit`/`_member`
      helpers so a member's id, colour, config and folder match what the real
      resolver produces (pure translation, no ops — `mates.expand`);
    * **polar pattern**, **mate**, **sub-assembly** → the kernel composes those
      transforms, so the instance composites once at its **stored base
      transform** (and a sub-assembly instance carries no ``part`` at all, so
      `_instance_rows` skips it outright);
    * anything whose pattern dict is malformed → the base instance, because a
      thumbnail must degrade, never refuse: a `ValidationError` escaping here
      would be a 422 out of an ``<img>`` tag.

    A thumbnail is a hint, not a measurement — `render_view` and
    `get_assembly` are what render the resolved truth.
    """
    rows: list = []
    for inst in service.store.instances(proj):
        # Defensive: `origin_project` is transient and `to_manifest` omits it,
        # so a stored instance never carries one. If a future writer let one
        # through, its part id belongs to ANOTHER project — looking it up here
        # would silently composite a same-id part of this project's geometry.
        if getattr(inst, "origin_project", None) is not None:
            continue
        if inst.assembly is not None or inst.mate or inst.pattern is None:
            rows.append(inst)
            continue
        try:
            rows.extend(_expand_linear(inst))
        except Exception:  # noqa: BLE001 — deliberate, and not a shortcut for
            # an enumeration nobody finished. `inst.pattern` is whatever the
            # manifest holds: `_validate_pattern` never checks `axis`, so a
            # one-entry axis is API-reachable through `set_pattern` and raises
            # IndexError; a hand-edited `"pattern": "polar"` raises
            # AttributeError; a bad count raises TypeError or ValueError. Two
            # rounds of review found two classes the tuple had missed, which is
            # the argument: this runs behind an <img> tag, where the only
            # honest failure mode is to draw the base instance and move on. A
            # thumbnail degrades; it never refuses, and it never 500s.
            rows.append(inst)
    return rows


def _expand_linear(inst) -> list:
    """A linear pattern's members, composed exactly as `mates.expand` does.

    Any other kind (polar today) needs the kernel to compose its per-member
    rotation, so the base instance is returned unexpanded.
    """
    if mates is None or inst.pattern.get("kind") != "linear":
        return [inst]
    axis = inst.pattern.get("axis")
    unit = mates._unit(axis[1] if axis else [1, 0, 0])
    step = float(inst.pattern["step_mm"])
    count = int(inst.pattern["count"])
    return [
        mates._member(
            inst, i,
            [inst.position[a] + i * step * unit[a] for a in range(3)],
            inst.rotation_deg,
        )
        for i in range(count)
    ]


def _instance_rows(service, proj: str) -> list[tuple[dict, object]]:
    """``(row, instance)`` for every instance whose mesh is already on disk.

    ``row`` is the identity that goes into the composite key: the instance's
    own mesh cache key (resolved through its configuration binding, purely) plus
    the placement that key does not cover. An instance with no mesh, a
    sub-assembly reference (no ``part`` of its own), or a binding its part no
    longer declares is skipped — the same "unbuildable instances are skipped"
    rule `render_view` and `packet._render_assembly` follow.

    ``_cache_key_for`` reads and hashes the part's script, so it is memoized
    per ``(part, config)`` for the length of one call: an assembly of two
    hundred instances of ten parts hashes ten scripts, not two hundred.
    """
    cache = service.store.cache_dir(proj)
    keys: dict[tuple[str, str | None], str | None] = {}
    rows = []
    for inst in _instances(service, proj):
        if not inst.part:
            continue
        memo = (inst.part, inst.config)
        if memo not in keys:
            try:
                record = service._record_for(proj, inst.part, inst.config)
                keys[memo] = service._cache_key_for(proj, record)
            except AppError:
                keys[memo] = None
        key = keys[memo]
        if key is None or mesh_for_key(cache, key) is None:
            continue
        rows.append((
            {
                "key": key,
                "position": [float(v) for v in inst.position],
                "rotation_deg": [float(v) for v in inst.rotation_deg],
                "color": inst.color,
            },
            inst,
        ))
    return rows


def _digest(rows: list[dict]) -> str:
    # The rows are sorted by their *serialization*, not as tuples: a row holds
    # a None colour beside strings and `sorted()` would raise comparing them.
    # 32 hex characters, like a build cache key, so one route gate covers both.
    blob = "\n".join(sorted(json.dumps(row, sort_keys=True) for row in rows))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def assembly_key(service, proj: str) -> str | None:
    """Content id of the assembly composite, or None when nothing is built.

    It hashes exactly what the composite draws — each instance's mesh key, its
    placement and its colour — so moving one instance is a new key and a new
    file, and the immutable cache answer stays honest.

    Recorded, deferred: no read publishes this key, so a client can only learn
    it from a previous response's ``ETag`` and the assembly route's ``?k=``
    has no first-load source the way a part's ``thumb_key`` does.
    """
    rows = _instance_rows(service, proj)
    if not rows:
        return None
    return _digest([row for row, _inst in rows])


def assembly_thumb(service, proj: str) -> tuple[bytes, str] | None:
    """``(png, key)`` for the project's assembly, or None when nothing is built.

    Composites the placed instances (transform + colour, like `render_view`),
    caching at ``.cache/asm-<key>.thumb.png``. A project with no placed, built
    instance — or one whose composite is over ``render.MAX_TRIANGLES`` — falls
    back to its **first built part's** thumbnail, so a project of loose parts
    still gets a picture; nothing built at all is None.

    That fallback scan is **bounded** (`_FALLBACK_SCAN`). Finding a built part
    means hashing its script, and this is a *listing* call the dashboard makes
    once per project: a thousand-part project with nothing built would
    otherwise spend a thousand hashes to answer "no picture". Past the bound
    the answer is the placeholder, which is what the client draws for a 404
    anyway.
    """
    cache = service.store.cache_dir(proj)
    rows = _instance_rows(service, proj)
    if rows:
        key = _digest([row for row, _inst in rows])
        path = cache / f"{ASM_PREFIX}{key}.thumb.png"
        if path.is_file():
            try:
                return path.read_bytes(), key
            except OSError:
                pass
        meshes = []
        for row, inst in rows:
            mesh_file = mesh_for_key(cache, row["key"])
            mesh = _read_mesh(mesh_file) if mesh_file is not None else None
            if mesh is None:
                continue   # trimmed or corrupt between the walk and the read
            meshes.append({
                "positions": mesh["positions"], "normals": mesh["normals"],
                "indices": mesh["indices"],
                "transform": (inst.position, inst.rotation_deg),
                "color": inst.color,
            })
        # `_has_triangles` before `_render`, not after: an all-empty composite
        # would come back out of `render_acm` as a ValidationError, i.e. a 422
        # from an <img> tag. Falling through to the part fallback is the answer.
        if _has_triangles(meshes) and not _too_many_triangles(meshes):
            png = _render(meshes)
            ProjectStore._atomic_write(path, png)
            return png, key

    for entry in service.store.manifest(proj)["parts"][:_FALLBACK_SCAN]:
        got = part_thumb(service, proj, entry["id"])
        if got is not None:
            return got
    return None


def has_thumb(service, proj: str) -> bool:
    """Could this project show a thumbnail? **is_file checks only.**

    The dashboard's gate: it renders nothing, hashes nothing and reads no
    manifest — one `scandir` of ``.cache/`` with an early exit. A thumbnail
    already on disk is a yes; so is any mesh, because the route renders one on
    demand from it. Deliberately a *hint*: a stale mesh whose key no part
    currently maps to makes it optimistic, and the cost of being wrong is an
    ``<img>`` that 404s into its placeholder.
    """
    try:
        # `canonical_path_of`, not `cache_dir`: the latter mkdirs, and a *gate*
        # must not create a directory in a project just by being asked about it.
        cache = service.store.canonical_path_of(proj) / ".cache"
    except AppError:
        return False
    try:
        with os.scandir(cache) as entries:
            for entry in entries:
                if entry.name.endswith((".thumb.png", ".acm")):
                    return True
    except OSError:
        return False
    return False


# ------------------------------------------------------------------ warmer

#: Put into our own subscriber queue to wake the thread out of a blocking
#: `get()` — the `app._wake_websocket_event_waiter` precedent. It is not an
#: event and never reaches `_on_event`.
_WAKE = object()


class ThumbnailWarmer:
    """Pre-renders thumbnails off the build path, one at a time.

    One daemon thread consumes ``service.bus.subscribe()`` and turns every
    ``rebuild_finished`` that carries a ``cache_key`` into a queued render.
    ``config``-tagged events are ignored on purpose: a configuration matrix
    build is not a tree row, and warming its key would evict the rows that are.

    The pending set is bounded and **coalesced by ``(project, key)``**; when it
    is full the **oldest** entry is dropped, because freshness catches up on the
    next read while a stale backlog never would. A render failure is counted and
    printed to stderr — never raised: a thumbnail that did not appear must not
    be able to take a build path or a server thread with it.
    """

    def __init__(self, service, *, maxsize: int = 256) -> None:
        self.service = service
        self.maxsize = maxsize
        self.stats = {
            "rendered": 0,          # a PNG was written
            "skipped_exists": 0,    # already cached (the common case)
            "skipped_missing": 0,   # the mesh is gone (trimmed, or never built)
            "skipped_too_large": 0, # over render.MAX_TRIANGLES, or empty
            "dropped": 0,           # evicted from a full queue, oldest first
            "failed": 0,            # the render raised; logged, never re-raised
        }
        self._pending: OrderedDict[tuple[str, str], None] = OrderedDict()
        #: `drain()` barriers, signalled by the thread only at the end of a
        #: cycle that left BOTH the bus queue and the pending set empty.
        self._barriers: list[threading.Event] = []
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._queue: queue.Queue | None = None
        self._stop = threading.Event()
        self._active = False

    # -- lifecycle ---------------------------------------------------------

    @property
    def started(self) -> bool:
        return self._thread is not None

    def start(self) -> None:
        """Subscribe and run. Idempotent — a second call is a no-op."""
        with self._lock:
            if self._thread is not None:
                return
            self._stop.clear()
            self._queue = self.service.bus.subscribe()
            self._thread = threading.Thread(
                target=self._run, name="agentcad-thumbnails", daemon=True)
            thread = self._thread
        thread.start()

    def stop(self) -> None:
        """Unsubscribe and join. Safe to call twice, and safe if never started."""
        with self._lock:
            thread, q = self._thread, self._queue
            self._thread = None
        if thread is None:
            return
        self._stop.set()
        if q is not None:
            self.service.bus.unsubscribe(q)
            try:
                q.put_nowait(_WAKE)   # break the blocking get()
            except queue.Full:
                pass
        thread.join(timeout=5.0)
        with self._lock:
            self._queue = None
            # A `drain()` racing a `stop()` would otherwise wait out its whole
            # timeout for a thread that is gone.
            self._release_barriers()

    def _release_barriers(self) -> None:
        """Signal every waiting `drain()`. Callers hold ``_lock``."""
        for barrier in self._barriers:
            barrier.set()
        self._barriers.clear()

    # -- queue -------------------------------------------------------------

    def enqueue(self, proj: str, key: str) -> None:
        """Ask for ``(proj, key)`` to be warmed. Coalesces; drops the oldest."""
        entry = (proj, key)
        with self._lock:
            if entry in self._pending:
                return   # already queued: coalesce, and keep its place
            while len(self._pending) >= self.maxsize:
                self._pending.popitem(last=False)
                self.stats["dropped"] += 1
            self._pending[entry] = None
            q = self._queue
        if q is not None:
            try:
                q.put_nowait(_WAKE)
            except queue.Full:
                pass   # the thread is already busy; it will see the entry

    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    def pending(self) -> list[tuple[str, str]]:
        """The queued ``(project, key)`` pairs, oldest first."""
        with self._lock:
            return list(self._pending)

    def drain(self, timeout: float = 10.0) -> None:
        """Block until the queue is empty and no render is in flight.

        The test seam. When the thread is **not** running this drains inline in
        the caller's thread, which is what makes a coalescing or drop-oldest
        test deterministic — either way the postcondition is the same one.

        A **barrier**, not a poll of "does it look idle": between `get()`
        returning an event and the thread recording that it is busy there is a
        window in which the queue is empty, nothing is pending and no render is
        running, and a polling drain returned early through it. The thread sets
        the barrier itself, and only at the end of a cycle that genuinely left
        both the queue and the pending set empty — so an event published before
        this call is always consumed before it returns.
        """
        if self._thread is None:
            self._render_pending()
            return
        barrier = threading.Event()
        with self._lock:
            q = self._queue
            self._barriers.append(barrier)
        if q is not None:
            try:
                q.put_nowait(_WAKE)   # make sure a cycle actually happens
            except queue.Full:
                pass                  # already busy; its cycle will signal
        if not barrier.wait(timeout):
            raise TimeoutError("thumbnail warmer did not drain")

    # -- thread ------------------------------------------------------------

    def _run(self) -> None:
        q = self._queue
        while not self._stop.is_set() and q is not None:
            try:
                event = q.get()
            except Exception:  # noqa: BLE001 — a torn-down queue ends the loop
                break
            if self._stop.is_set():
                break
            with self._lock:
                self._active = True
            try:
                self._absorb(event)
                while True:
                    try:
                        self._absorb(q.get_nowait())
                    except queue.Empty:
                        break
                self._render_pending()
            finally:
                with self._lock:
                    self._active = False
                    if q.empty() and not self._pending:
                        self._release_barriers()

    def _absorb(self, event) -> None:
        if isinstance(event, dict):
            self._on_event(event)

    def _on_event(self, event: dict) -> None:
        if event.get("type") != "rebuild_finished":
            return
        # A configuration build tags its events with `config`; the key is
        # absent entirely on a working-state rebuild (`_build_with`'s `tag`).
        if "config" in event:
            return
        proj, key = event.get("project"), event.get("cache_key")
        if isinstance(proj, str) and isinstance(key, str) and proj and key:
            self.enqueue(proj, key)

    def _render_pending(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                if not self._pending:
                    return
                (proj, key), _ = self._pending.popitem(last=False)
            self._render_one(proj, key)

    def _render_one(self, proj: str, key: str) -> None:
        try:
            cache = self.service.store.cache_dir(proj)
        except Exception:  # noqa: BLE001 — a project deleted since the build
            self.stats["skipped_missing"] += 1
            return
        if thumb_path(cache, key).is_file():
            self.stats["skipped_exists"] += 1
            return
        if mesh_for_key(cache, key) is None:
            self.stats["skipped_missing"] += 1
            return
        try:
            png = render_part_thumb(cache, key)
        except Exception as exc:  # noqa: BLE001 — see the class docstring
            self.stats["failed"] += 1
            print(f"[thumbnails] {proj}/{key}: render failed: {exc}",
                  file=sys.stderr)
            return
        if png is None:
            self.stats["skipped_too_large"] += 1
        else:
            self.stats["rendered"] += 1
