FROM node:14-alpine3.14 AS dev

# Define some ENV Vars
ENV PORT=80 \
  DIRECTORY=/app \
  IS_DOCKER=true

WORKDIR ./app

COPY dashy/package.json dashy/yarn.lock ./

RUN yarn

COPY dashy ./

RUN yarn build

# Expose given port
EXPOSE ${PORT}

CMD yarn build-and-start