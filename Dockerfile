FROM python:3.10-slim

WORKDIR /app

RUN pip install --no-cache-dir fastapi uvicorn httpx

COPY main.py .

EXPOSE 1566

CMD ["python", "main.py"]