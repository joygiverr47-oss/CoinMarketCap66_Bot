FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py .

# Do NOT declare ARG/ENV for BOT_TOKEN here.
# Railway (and `docker run -e BOT_TOKEN=...`) injects it into the
# container's runtime environment automatically — the app reads it
# via os.environ at process start, so it never gets baked into a layer.

CMD ["python", "bot.py"]
