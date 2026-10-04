FROM node:24.21.0-bookworm-slim

ENV NODE_ENV=development
WORKDIR /app
RUN chown node:node /app
USER node

COPY --chown=node:node package.json package-lock.json ./
RUN npm ci --ignore-scripts

COPY --chown=node:node . .

EXPOSE 5173

CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
