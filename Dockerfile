# Agent Farm — Cloud Run container.
# Needs BOTH Python (the four FastAPI services + OR-Tools) and Node.js
# (the MCP bridge spawns `npx mongodb-mcp-server` at runtime for the
# "ASK GEMINI" situation report). nginx fronts the four services so the
# frontend's relative /api/... paths route to the right service on one port.

FROM python:3.12-slim

# --- system deps: Node.js (for npx mongodb-mcp-server) + nginx (reverse proxy) ---
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl gnupg nginx \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*
RUN npm install -g mongodb-mcp-server@latest
WORKDIR /app

# --- python deps ---
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# --- app code ---
COPY . .

# nginx config + startup script
COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
COPY deploy/start.sh /app/deploy/start.sh
RUN chmod +x /app/deploy/start.sh

# Cloud Run provides $PORT (default 8080). nginx listens on it; the four
# uvicorn services run on internal ports 8000/8001/8002/8090.
ENV PORT=8080
EXPOSE 8080

CMD ["/app/deploy/start.sh"]
