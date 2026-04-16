FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir \
    requests \
    paho-mqtt

COPY app/ /app/

CMD ["python", "-u", "main.py"]
