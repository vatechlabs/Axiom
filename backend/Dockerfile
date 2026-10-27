FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --timeout 180 --retries 10 --index-url https://pypi.org/simple -r requirements.txt
COPY . .
EXPOSE 8000
CMD ["gunicorn","portal.wsgi:application","--bind","0.0.0.0:8000","--workers","3"]
