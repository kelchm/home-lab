FROM python:3.15.1-alpine@sha256:54448f67d7c31c39e5cdd3ea2d6382395ce142806c284425f8e897d78a0465b3
ARG MODE=good
ENV FIXTURE_MODE=${MODE}
LABEL org.opencontainers.image.source="https://github.com/kelchm/home-lab" \
      org.opencontainers.image.revision="1111111111111111111111111111111111111111"
COPY server.py /server.py
USER 1000:1000
CMD ["python", "/server.py"]
