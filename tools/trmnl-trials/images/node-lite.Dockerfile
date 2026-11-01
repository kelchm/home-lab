FROM node:26-bookworm-slim@sha256:bc0a2afd3b74ce12222fc8bab99271e66257f021078f5f4a5d687a77d280c1d9
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
