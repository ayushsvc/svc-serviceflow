FROM node:22-alpine AS build
WORKDIR /app
COPY package*.json ./
COPY client/package*.json client/
COPY server/package*.json server/
RUN npm install
COPY client client
RUN npm run build --workspace client

FROM node:22-alpine
WORKDIR /app
ENV NODE_ENV=production
COPY package*.json ./
COPY client/package*.json client/
COPY server/package*.json server/
RUN npm install --omit=dev
COPY --from=build /app/client/dist client/dist
COPY server server
EXPOSE 4000
CMD ["npm","start","--workspace","server"]
