FROM python:3.12-slim
WORKDIR /app
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN python -m playwright install --with-deps chromium && chmod -R a+rX /ms-playwright
COPY . .
RUN useradd --create-home atlas && chown -R atlas:atlas /app
USER atlas
ENV HOME=/home/atlas
ENV XDG_CACHE_HOME=/tmp/.cache
CMD ["uvicorn","atlas.browser_service:app","--host","0.0.0.0","--port","8787","--no-access-log"]
