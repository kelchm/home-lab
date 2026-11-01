FROM elixir:1.20-otp-27-slim@sha256:e33ed7f7bf98fcc1db795bf5eb5bd8c1ccb4c5332fc2c69ee8197a0053d010df
RUN apt-get update && apt-get install -y --no-install-recommends build-essential git ca-certificates curl nodejs npm chromium imagemagick fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
ENV MIX_ENV=prod PUPPETEER_SKIP_DOWNLOAD=true PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium ERL_FLAGS="+S 4:4 +JMsingle true"
WORKDIR /app
COPY mix.exs mix.lock ./
RUN mix local.hex --force && mix local.rebar --force && mix deps.get --only prod && mix deps.compile
COPY . .
# Render this pod's screen through loopback instead of the shared admin Gateway.
# Keep Endpoint.url() unchanged for the HTTPS image URLs returned to devices.
RUN grep -Fq '#{TrmnlWeb.Endpoint.url()}/screens/' lib/trmnl/screen.ex && \
    sed -i 's|#{TrmnlWeb.Endpoint.url()}/screens/|http://127.0.0.1:#{System.get_env("PORT", "4000")}/screens/|' lib/trmnl/screen.ex && \
    mix assets.setup && mix assets.deploy && mix compile
COPY .trial/phoenix-package.json /opt/puppeteer/package.json
COPY .trial/phoenix-package-lock.json /opt/puppeteer/package-lock.json
RUN cd /opt/puppeteer && npm ci --omit=dev --no-audit && ln -s /opt/puppeteer/node_modules/.bin/puppeteer-img /usr/local/bin/puppeteer-img
# Debian's ImageMagick 6 exposes convert; upstream calls the v7 magick name.
RUN if ! command -v magick >/dev/null; then ln -s /usr/bin/convert /usr/local/bin/magick; fi
RUN useradd --uid 1000 --create-home app && mkdir -p /data priv/static/generated && cp -a /root/.mix /home/app/.mix && cp -a /root/.hex /home/app/.hex && chown -R app:app /app /data /home/app && rm -rf /tmp/mix_*
ENV PHX_SERVER=true
USER 1000:1000
EXPOSE 4000
CMD ["mix", "phx.server"]
