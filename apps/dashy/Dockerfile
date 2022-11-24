FROM node:16-alpine AS dev

# Define some ENV Vars
ENV PORT=80 \
  DIRECTORY=/app \
  IS_DOCKER=true

WORKDIR ./app

COPY dashy/package.json dashy/yarn.lock ./

RUN yarn install --frozen-lockfile --network-timeout 1000000

COPY dashy ./
COPY conf.yml ./public/

RUN yarn build --mode production

# Expose given port
EXPOSE ${PORT}

CMD yarn build-and-start

FROM nginx:alpine AS Prod

COPY --from=dev ./app/dist /usr/share/nginx/html/
