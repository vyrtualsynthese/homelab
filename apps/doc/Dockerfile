FROM node:lts-alpine AS dev

WORKDIR ./app

COPY package*.json ./

RUN npm i --ignore-scripts

COPY . .

RUN npm run build

FROM nginx:alpine AS Prod

COPY --from=dev ./app/build /usr/share/nginx/html/
