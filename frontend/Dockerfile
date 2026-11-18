FROM node:26-alpine AS development-dependencies-env
COPY . /app
WORKDIR /app
RUN npm ci

FROM node:26-alpine AS production-dependencies-env
COPY ./package.json package-lock.json /app/
WORKDIR /app
RUN npm ci --omit=dev

# Help, built from the core's docs (docs/help and docs/theme, the named build context "docs"): the console
# serves it at /help, so every install has the help of its own release, offline too.
FROM python:3.15-slim AS help-env
RUN pip install --no-cache-dir zensical==0.0.67
COPY --from=docs help /docs/help
COPY --from=docs theme /docs/theme
RUN python /docs/help/scripts/build.py --target console --out /help-build --build

FROM node:26-alpine AS build-env
COPY . /app/
COPY --from=development-dependencies-env /app/node_modules /app/node_modules
WORKDIR /app
RUN npm run build

FROM node:26-alpine
# The Alpine packages' fixes (zlib 1.3.2-r1 had a critical advisory, ALPINE-CVE-2026-4738), and an npm whose own
# undici and brace-expansion are fixed (npm 11.19.1 in node:24 bundles undici 6.28.0 and brace-expansion 5.0.9:
# GHSA-2m5v-pch6-74hx and others, fixed in 6.29.1 and 5.0.11). npm starts the server (CMD below).
RUN apk upgrade --no-cache \
    && npm install -g --no-fund --no-audit npm@11.21.2 \
    && npm cache clean --force
COPY ./package.json package-lock.json /app/
COPY ./LICENSE ./NOTICE /app/
COPY --from=production-dependencies-env /app/node_modules /app/node_modules
COPY --from=build-env /app/build /app/build
COPY --from=help-env /help-build/site /app/build/client/help
WORKDIR /app
# What the image is, as GHCR shows it (the same as .github/workflows/docker-publish.yml; cli/tests/test_image_labels.py).
LABEL org.opencontainers.image.title="Fadenstack console" \
      org.opencontainers.image.description="The Fadenstack console: the web interface for chat and for administering a Fadenstack installation, served by Node.js (React Router). It also serves the Help pages of its own release at /help, so they are there without internet access." \
      org.opencontainers.image.source="https://github.com/fadenstack/fadenstack" \
      org.opencontainers.image.licenses="Apache-2.0"
CMD ["npm", "run", "start"]
