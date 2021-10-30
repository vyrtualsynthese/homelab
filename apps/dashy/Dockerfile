FROM node:lts-alpine3.14 AS dev

# Define some ENV Vars
ENV PORT=80 \
  DIRECTORY=/app \
  IS_DOCKER=true

WORKDIR ./app

COPY dashy/package.json dashy/yarn.lock ./

RUN yarn install

COPY dashy ./
COPY conf.yml ./public/

RUN yarn build

# Expose given port
EXPOSE ${PORT}

CMD yarn build-and-start

FROM nginx:alpine AS Prod

COPY --from=dev ./app/dist /usr/share/nginx/html/
