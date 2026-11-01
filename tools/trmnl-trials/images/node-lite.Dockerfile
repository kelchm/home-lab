FROM node:22-bookworm-slim@sha256:4b150b48b4116e2a22f2c39cc27025fbf4f153e2078fd3b14d159fdb5c7279da
RUN apt-get update && apt-get install -y --no-install-recommends chromium fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
WORKDIR /app
ENV PUPPETEER_SKIP_DOWNLOAD=true PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium NODE_ENV=production
COPY package.json .trial/node-package-lock.json ./
RUN mv node-package-lock.json package-lock.json && npm ci --omit=dev --no-audit
COPY . .
# Upstream ships BYOS disabled in a source constant. Expose only the settings
# needed for the trial; cloud proxying and firmware updates stay disabled.
COPY .trial/node-config.mjs /tmp/node-config.mjs
RUN node /tmp/node-config.mjs
USER 1000:1000
EXPOSE 3000
CMD ["node", "--import", "tsx", "src/Main.ts"]
