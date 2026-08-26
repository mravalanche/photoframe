FROM python:3.12-slim

WORKDIR /photoframe

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "photoframe:create_app()"]
