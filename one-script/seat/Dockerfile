# The one script's model seat: Claude Code with two doors and nothing else.
#
#   cd one-script
#   docker build -f seat/Dockerfile -t onescript-seat .
#   docker run --rm -it -v "$SERVED_DIR":/served:ro -v "$WORK_DIR":/work \
#       -e ANTHROPIC_API_KEY onescript-seat
#
# Managed settings only reach this container, so the operator's other seats
# keep their tools. The doors are root's; the model runs as `seat`.
ARG NODE_BASE=node:22-slim
ARG PY_BASE=python:3.12-slim
FROM ${NODE_BASE} AS node

# Python from its own image, Node copied in: no package manager runs here.
FROM ${PY_BASE}
ARG CLAUDE_CODE_VERSION=2.1.293
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && npm install -g "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
 && npm cache clean --force

COPY hook.py prompt.py onescript/doors.py seat/doors.json /opt/onescript/
COPY seat/managed-settings.json /etc/claude-code/managed-settings.json
RUN chown -R root:root /opt/onescript /etc/claude-code \
 && chmod 0755 /opt/onescript /etc/claude-code \
 && chmod 0644 /opt/onescript/* /etc/claude-code/managed-settings.json \
 && useradd --create-home --uid 1001 seat \
 && mkdir -p /served /work && chown seat:seat /work

USER seat
WORKDIR /work
# The doors are checked before Claude Code starts; if one isn't shut, it doesn't.
ENTRYPOINT ["python3", "/opt/onescript/doors.py", "exec", "--pins", "/opt/onescript/doors.json", "--", "claude"]
