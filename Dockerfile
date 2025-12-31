
FROM python:3.11-slim

WORKDIR /app

# Instala dependências do sistema
RUN apt-get update && apt-get install -y \
    libffi-dev libssl-dev build-essential git && \
    rm -rf /var/lib/apt/lists/*

# Copia arquivos do projeto
COPY . /app

# Instala dependências Python
RUN pip install --no-cache-dir -r requirements.txt

# Define porta
ENV PORT=80
EXPOSE 80

# Comando para iniciar Anthias
CMD ["python", "server.py"]
