# Stage 1: Build static React Vite application
FROM node:20-alpine AS builder

WORKDIR /app

COPY package*.json ./
RUN npm ci --prefer-offline --no-audit

COPY . .

# Accept build-time env vars so Vite can bake them into the bundle
ARG VITE_API_URL
ARG VITE_COPILOT_URL
ARG VITE_BACKEND_URL
ARG VITE_MICROSOFT_CLIENT_ID
ARG VITE_MICROSOFT_TENANT_ID
ARG VITE_MICROSOFT_REDIRECT_URL
ARG VITE_MICROSOFT_API_SCOPE

ENV VITE_API_URL=$VITE_API_URL
ENV VITE_COPILOT_URL=$VITE_COPILOT_URL
ENV VITE_BACKEND_URL=$VITE_BACKEND_URL
ENV VITE_MICROSOFT_CLIENT_ID=$VITE_MICROSOFT_CLIENT_ID
ENV VITE_MICROSOFT_TENANT_ID=$VITE_MICROSOFT_TENANT_ID
ENV VITE_MICROSOFT_REDIRECT_URL=$VITE_MICROSOFT_REDIRECT_URL
ENV VITE_MICROSOFT_API_SCOPE=$VITE_MICROSOFT_API_SCOPE

RUN npm run build

# Stage 2: Serve static production assets with Nginx
FROM nginx:alpine

COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
