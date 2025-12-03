FROM python:3.11-slim

WORKDIR /app

# Instala dependências do sistema necessárias para cryptography
RUN apt-get update && apt-get install -y \
    gcc \
    libssl-dev \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements_certificado.txt .
RUN pip install --no-cache-dir -r requirements_certificado.txt

COPY api_certificado.py .

EXPOSE 8000

CMD ["uvicorn", "api_certificado:app", "--host", "0.0.0.0", "--port", "8000"]
