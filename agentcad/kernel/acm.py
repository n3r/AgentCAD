"""ACM1 binary mesh format — pack/parse. Little-endian throughout.

Layout:
    4 bytes  magic ``ACM1``
    u32      nv        vertex count
    u32      nt        triangle count
    u32      nep       total edge polyline points
    u32      nel       edge polyline count
    f32[3*nv]  positions
    f32[3*nv]  normals (unit)
    u32[3*nt]  triangle indices (into positions)
    u32[nel]   polyline lengths (sum == nep)
    f32[3*nep] edge points, concatenated per polyline

This module has no OCP dependency so any process can read meshes.
"""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np

MAGIC = b"ACM1"
_HEADER = struct.Struct("<4sIIII")


def pack(
    positions: np.ndarray,
    normals: np.ndarray,
    indices: np.ndarray,
    edge_lengths: np.ndarray,
    edge_points: np.ndarray,
) -> bytes:
    positions = np.ascontiguousarray(positions, dtype="<f4").reshape(-1, 3)
    normals = np.ascontiguousarray(normals, dtype="<f4").reshape(-1, 3)
    indices = np.ascontiguousarray(indices, dtype="<u4").reshape(-1, 3)
    edge_lengths = np.ascontiguousarray(edge_lengths, dtype="<u4").reshape(-1)
    edge_points = np.ascontiguousarray(edge_points, dtype="<f4").reshape(-1, 3)
    if len(normals) != len(positions):
        raise ValueError("normals/positions length mismatch")
    if len(edge_points) != int(edge_lengths.sum()):
        raise ValueError("edge point count does not match polyline lengths")
    header = _HEADER.pack(
        MAGIC, len(positions), len(indices), len(edge_points), len(edge_lengths)
    )
    return b"".join(
        [
            header,
            positions.tobytes(),
            normals.tobytes(),
            indices.tobytes(),
            edge_lengths.tobytes(),
            edge_points.tobytes(),
        ]
    )


def parse(data: bytes) -> dict:
    magic, nv, nt, nep, nel = _HEADER.unpack_from(data, 0)
    if magic != MAGIC:
        raise ValueError("not an ACM1 buffer")
    off = _HEADER.size

    def take(dtype: str, count: int, width: int) -> np.ndarray:
        nonlocal off
        arr = np.frombuffer(data, dtype=dtype, count=count * width, offset=off)
        off += arr.nbytes
        return arr.reshape(-1, width) if width > 1 else arr

    positions = take("<f4", nv, 3)
    normals = take("<f4", nv, 3)
    indices = take("<u4", nt, 3)
    edge_lengths = take("<u4", nel, 1)
    edge_points = take("<f4", nep, 3)
    return {
        "positions": positions,
        "normals": normals,
        "indices": indices,
        "edge_lengths": edge_lengths,
        "edge_points": edge_points,
    }


def read(path: str | Path) -> dict:
    return parse(Path(path).read_bytes())
