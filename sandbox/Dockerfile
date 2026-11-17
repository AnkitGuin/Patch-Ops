FROM python:3.12.12-slim-bookworm AS wheels
COPY sandbox/wheelhouse.in /opt/patchops/wheelhouse.in
RUN python -m pip download --only-binary=:all: --dest /wheelhouse -r /opt/patchops/wheelhouse.in \
 && python -m pip download --only-binary=:all: --dest /wheelhouse 'pydantic==1.10.22' \
 && python -m pip download --only-binary=:all: --dest /wheelhouse 'pydantic==2.11.10'
FROM python:3.12.12-slim-bookworm
COPY --from=wheels /wheelhouse /wheelhouse
COPY sandbox/tools.txt /opt/patchops/tools.txt
RUN mkdir -p /work
COPY src/patchops/sandbox_runtime/runner.py /opt/patchops/runner.py
USER 10001:10001
ENTRYPOINT ["python", "-I", "/opt/patchops/runner.py"]
