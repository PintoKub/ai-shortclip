FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg curl ca-certificates gnupg \
 && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
 && apt-get install -y --no-install-recommends nodejs fonts-noto-color-emoji \
 && rm -rf /var/lib/apt/lists/*
RUN npm install -g @anthropic-ai/claude-code
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && playwright install --with-deps chromium
COPY . .
ENV SHORTCLIP_HOST=0.0.0.0 SHORTCLIP_INSECURE_COOKIE=1 SHORTCLIP_NO_SYSTEMD=1
EXPOSE 8099
VOLUME ["/app/projects", "/app/db", "/app/plans", "/app/secrets"]
CMD ["python", "dashboard/server.py"]
