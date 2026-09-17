FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY run.py .
COPY agents/ agents/
COPY rca/ rca/

CMD ["python", "run.py", "--help"]
