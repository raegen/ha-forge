FROM ghcr.io/home-assistant/base:latest

ARG BUILD_VERSION
ARG BUILD_ARCH

RUN apk add --no-cache python3 py3-pillow ca-certificates patch diffutils grep findutils git openssh-client-default

COPY run.sh /
COPY forge.py forge_safety.py forge_store.py forge_provider.py forge_runtime.py forge_attachments.py forge_models.py forge_ssh.py forge_git.py /
COPY static /static
COPY tests /tests
RUN chmod a+x /run.sh /forge.py \
    && python3 -m py_compile /forge.py /forge_safety.py /forge_store.py /forge_provider.py /forge_runtime.py /forge_attachments.py /forge_models.py /forge_ssh.py /forge_git.py \
    && python3 -m unittest discover -s /tests -v

LABEL \
  io.hass.version="${BUILD_VERSION}" \
  io.hass.type="app" \
  io.hass.arch="${BUILD_ARCH}"

CMD ["/run.sh"]
