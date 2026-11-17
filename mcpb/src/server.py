"""Entry point of the Claude Desktop bundle: runs onshape-jamcp (an MCP server over stdio).

`uv run src/server.py` starts this file inside the environment built from the bundle's
pyproject.toml, which installs onshape-jamcp from PyPI. Settings arrive as ONSHAPE_* environment
variables, filled in from the form Claude Desktop shows when the extension is installed.
"""

from onshape_jamcp.__main__ import main

if __name__ == "__main__":
    main()
