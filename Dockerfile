# The image is the unit that gets scanned, load tested and deployed, so it is
# also the thing the tests should run against. test_serve.py takes a base URL
# for exactly that reason: CI points it at this container, not at the source.
#
# ponytail: single stage. There is nothing to compile -- numpy ships wheels --
# so a builder stage would copy files around to save nothing. Revisit if a
# dependency ever needs a C toolchain, because that toolchain in the runtime
# image is both weight and attack surface.
FROM python:3.12-slim

# Pinned to a digest-free tag on purpose: Trivy runs on every build, so a base
# image that picks up patches is a feature here. An unpatched pin that fails the
# CVE gate every week is how teams learn to pass `--exit-code 0`.

# KB_READ_ONLY_CACHE: a server never rewrites vectors.json. New questions are
# embedded into a bounded in-memory cache instead; see rag._embed_uncached for
# the two ways the development behaviour would fail in public.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    KB_READ_ONLY_CACHE=1 \
    PORT=8080

WORKDIR /app

# Patch the base image before installing anything.
#
# Not decoration. The first real run of the CVE gate refused this image over
# three criticals in perl-base -- a regular expression heap overflow and a path
# traversal in Archive::Tar -- all with a patched version already published.
# `python:3.12-slim` carries perl as a base dependency and is rebuilt on its own
# schedule, so an image built today can ship a fix that has existed for weeks.
#
# A blanket upgrade rather than a named package, because next month it is a
# different package and a list of names is a list somebody has to maintain
# against an adversary who is not consulting it. The cost is that two builds of
# the same commit can differ, which is the correct trade for a base layer: the
# alternative is a reproducible build of a known-vulnerable image.
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# Dependencies before source, so editing a .py file does not reinstall numpy.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY rag.py tools.py agent.py serve.py ./
COPY docs/ docs/
# Embeddings for the corpus, computed once and committed. Without this the
# container would need an API key at startup just to build its index, which
# would make retrieval -- the part that needs no model -- depend on one.
COPY vectors.json ./

# Nothing here needs to write to the filesystem, and root inside a container is
# root on the host if anything ever escapes it.
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app \
    && chown -R app:app /app
USER 10001

EXPOSE 8080

# Layer 2 will kill this container mid-request and watch what the orchestrator
# does. That only works if the orchestrator can tell healthy from merely running.
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request,os,sys; \
sys.exit(0 if urllib.request.urlopen(f\"http://127.0.0.1:{os.environ['PORT']}/health\", timeout=2).status == 200 else 1)"]

CMD ["python", "serve.py"]
