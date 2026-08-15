FROM python:3.12-slim

# 7zz handles .7z/.rar and any zip pyzipper can't (incl. odd AES variants)
RUN apt-get update \
 && apt-get install -y --no-install-recommends 7zip p7zip-full \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY watcher.py extractor.py make_session.py ./

ENV DOWNLOAD_DIR=/data/downloads STATE_FILE=/data/state.json
VOLUME /data
CMD ["python", "-u", "watcher.py"]
