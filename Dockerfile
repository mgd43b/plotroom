FROM python:3.14-slim-trixie

ENV UV_PYTHON_INSTALL_DIR=/opt/uv-python \
    UV_PYTHON_PREFERENCE=only-managed \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"
WORKDIR /app
RUN /usr/local/bin/python -m pip install --no-cache-dir uv==0.13.8
COPY pyproject.toml uv.lock .python-version README.md ./
COPY src/ src/
RUN uv sync --locked --no-dev --no-editable \
    && python -c 'from plotroom.app.sqlite_floor import floor_problem; raise SystemExit(floor_problem())' \
    && groupadd --gid 10001 plotroom \
    && useradd --uid 10001 --gid 10001 --create-home plotroom \
    && install -d -o plotroom -g plotroom -m 0700 /data
USER plotroom
ENTRYPOINT ["plotroom"]
CMD ["serve"]
