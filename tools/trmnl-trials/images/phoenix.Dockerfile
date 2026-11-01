FROM elixir:1.18-otp-27-slim@sha256:2c226386b099c8ff2c423a2ebb3e1e53d243338a743226e79df1453479019ad6
RUN apt-get update && apt-get install -y --no-install-recommends build-essential git ca-certificates curl nodejs npm chromium imagemagick fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
ENV MIX_ENV=prod PUPPETEER_SKIP_DOWNLOAD=true PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium ERL_FLAGS="+S 4:4 +JMsingle true"
WORKDIR /app
COPY mix.exs mix.lock ./
RUN mix local.hex --force && mix local.rebar --force && mix deps.get --only prod && mix deps.compile
COPY . .
RUN mix assets.setup && mix assets.deploy && mix compile
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
