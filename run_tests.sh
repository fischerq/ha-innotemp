#!/bin/bash

# "python -m pytest" (not bare "pytest") so the tests run with the same
# interpreter that has homeassistant and aiohttp installed, even when another
# pytest (e.g. a uv/pipx tool install) comes first on PATH.
python -m pytest tests/ "$@"
