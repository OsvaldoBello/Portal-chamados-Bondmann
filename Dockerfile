# syntax=docker/dockerfile:1

# ============================================================
# Estágio 1 — build do CSS com Tailwind CLI (Seção 0.4 / C3)
# CSS compilado e purgado; servido como asset estático (sem CDN em prod).
# ============================================================
FROM node:22-slim AS css
WORKDIR /build
COPY package.json ./
RUN npm install --no-audit --no-fund
COPY tailwind.config.js ./
# Precisa do app/ inteiro, não só static/src e templates: o content scan do
# Tailwind (tailwind.config.js) também cobre app/static/js/**/*.js (classes
# aplicadas via JS) e app/**/*.py (paleta de rótulos de app/templating.py).
# Copiar só templates+src fazia o purge derrubar essas classes em produção
# (etiquetas do Kanban saíam sem cor mesmo com o CSS "correto" localmente).
COPY app ./app
RUN npx tailwindcss -i ./app/static/src/input.css -o ./app/static/css/app.css --minify

# ============================================================
# Estágio 2 — runtime Python 3.12 (Seção 0.2)
# Inclui libmagic (python-magic, Seção 3.9). Uvicorn na porta 8080 (Railway).
# ============================================================
FROM python:3.14-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# libmagic1 para validação de MIME por magic bytes
RUN apt-get update \
    && apt-get install -y --no-install-recommends libmagic1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
# CSS gerado no estágio anterior
COPY --from=css /build/app/static/css/app.css ./app/static/css/app.css

# Usuário não-root
RUN useradd -m appuser
USER appuser

EXPOSE 8080
# Railway exige a porta 8080; --proxy-headers para X-Forwarded-For (Seção 2.4)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips", "*"]
