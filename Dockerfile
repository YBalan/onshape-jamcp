# onshape-jamcp - MCP server speaking stdio.
#
#   docker build -t onshape-jamcp .
#   docker run -i --rm -e ONSHAPE_ACCESS_KEY=... -e ONSHAPE_SECRET_KEY=... onshape-jamcp
#
# The server starts (and lists its tools) without credentials, so registries such as Glama can
# introspect the image; tool calls report a clear error until the keys are provided.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install . && rm -rf /build

# Run as an unprivileged user; screenshots/exports go to a writable directory.
RUN useradd --create-home --uid 10001 jamcp
USER jamcp
WORKDIR /home/jamcp
ENV ONSHAPE_OUTPUT_DIR=/home/jamcp/output

ENTRYPOINT ["onshape-jamcp"]
