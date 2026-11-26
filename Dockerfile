FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends espeak-ng mbrola mbrola-de2 mbrola-de3 mbrola-de4 mbrola-de5 mbrola-de6 mbrola-de7 \
    tesseract-ocr ffmpeg curl bzip2 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server ./server
COPY web ./web
RUN python server/prepare_voices.py
ENV CHOR_DATA=/data
VOLUME /data
EXPOSE 8000
WORKDIR /app/server
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
