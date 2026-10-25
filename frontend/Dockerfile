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
FROM python:3.12-slim AS help-env
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
COPY ./package.json package-lock.json /app/
COPY ./LICENSE ./NOTICE /app/
COPY --from=production-dependencies-env /app/node_modules /app/node_modules
COPY --from=build-env /app/build /app/build
COPY --from=help-env /help-build/site /app/build/client/help
WORKDIR /app
CMD ["npm", "run", "start"]
