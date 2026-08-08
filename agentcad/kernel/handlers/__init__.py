"""Worker handler packs. Each module here may export a ``HANDLERS`` dict or a
``register(toolbox) -> dict`` function; ``worker._load_handler_packs`` merges
them into the dispatch table at worker startup. See ``worker.WORKER_TOOLBOX``
for the shared build/metric/export/tessellate helpers packs receive."""
