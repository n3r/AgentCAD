# 0041 — Vision feedback: render_view tool with real image content

- **Commit:** pending
- **Date:** 2026-08-09
- **Author:** Claude (with Nikita Fedorov)

## Summary

Agents can now *see* what they build (roadmap "Vision feedback"): a
`render_view` tool rasterizes the built mesh — one part or the whole placed
assembly — to a shaded PNG entirely server-side (numpy + stdlib zlib; no GPU,
no new dependencies), delivered as genuine image content over MCP and in the
built-in chat.

## Changes

- **`agentcad/core/render.py`** (new): orthographic software rasterizer over
  ACM buffers — camera bases matching the drawing pack's view conventions,
  instance transforms replicated exactly from `service._apply_transform`
  (intrinsic XYZ, vectorized), 5%-margin bbox fit, z-buffered per-triangle
  barycentric fill, flat Lambert shading with two lights, and a ~30-line
  PNG encoder (8-bit RGB, filter 0). >500k total triangles raises a
  ValidationError naming the fix. ~54k triangles/sec at 800×600.
- **Tool** `render_view(project, part_id?, view, width, height)`
  (`tools_vision.py` pack): part or assembly (mates resolved, colors
  honored, unbuildable instances → `skipped`); writes
  `exports/renders/<name>_<view>.png` atomically; returns path + dimensions +
  `png_base64`.
- **Route** `POST /api/projects/{proj}/render` (`routes_vision.py`): success
  returns raw PNG bytes (`image/png`, no-store); tool errors pass through as
  JSON.
- **MCP**: results carrying `png_base64` become `[ImageContent,
  TextContent(JSON minus base64)]`; every other tool's content is unchanged.
- **Chat**: such results become a two-block tool_result (base64 image block +
  JSON text block) so the model actually sees the image; the
  `chat_tool_result` bus event replaces the base64 with `"<image omitted>"`.

## Files

- `agentcad/core/render.py`, `agentcad/core/tools_vision.py`,
  `agentcad/server/routes_vision.py`
- `agentcad/agent/mcp_server.py`, `agentcad/agent/chat.py`
- `tests/test_render.py` (6 tests: PNG structure/variance, silhouette ratio
  top vs front for a flat plate, assembly + color assertions, validation,
  route bytes), `tests/test_chat.py` (+image content pairing test),
  `tests/test_mcp.py` (live render_view call asserting real ImageContent)
- `docs/agent-api.md`, `docs/user-guide.md`

## Notes

Flat per-triangle shading (geometric normals flipped toward camera) is
deliberate — it sidesteps imported-STL crease-normal complexity and keeps
open meshes lit. The mcp 2.0 `ImageContent` camelCase alias round-trip is
verified end-to-end by the live MCP test.
