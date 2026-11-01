"""Serve upstream Django ASGI and its static assets for this isolated trial."""

from django.contrib.staticfiles.handlers import ASGIStaticFilesHandler
from byos_django.asgi import application as upstream_application

static_application = ASGIStaticFilesHandler(upstream_application)


async def application(scope, receive, send):
    # Firmware clients request these paths without a slash and some do not
    # follow Django's 301 redirect. Normalize internally, preserving query.
    if scope["type"] == "http" and scope["path"] in {"/api/setup", "/api/display"}:
        scope = dict(scope)
        scope["path"] += "/"
        scope["raw_path"] = scope["path"].encode("ascii")
    await static_application(scope, receive, send)
