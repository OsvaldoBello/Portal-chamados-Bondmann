#!/bin/sh
# Backup da sessão do WUZAPI (docs/wuzapi/) — roda dentro do container
# `wuzapi-backup` do docker-compose, com /dbdata montado read-only.
#
# Por que `sqlite3 .backup` e não `cp`: o whatsmeow abre o banco em modo WAL
# (`journal_mode(WAL)`) e escreve o tempo todo (chaves de sessão, prekeys,
# app state). Copiar o .db com o processo vivo tem chance real de pegar um
# arquivo sem as páginas do -wal, e o backup só se revela corrompido no dia
# em que você precisa dele. `.backup` usa a API de backup online do SQLite:
# consistente com o processo rodando, sem parar o container.
#
# O que é salvo:
#   main.db  -> identidade do device pareado + chaves de criptografia.
#               É ESTE arquivo que evita reescanear o QR Code.
#   users.db -> usuários do wuzapi (token, webhook, eventos assinados).
# Perder users.db custa um POST em /admin/users. Perder main.db custa um QR
# Code novo no celular — e, dependendo do humor do WhatsApp, um período de
# reaquecimento do número.

set -eu

ORIGEM="${ORIGEM:-/dbdata}"
DESTINO="${DESTINO:-/backups}"
RETENCAO_DIAS="${RETENCAO_DIAS:-14}"
CARIMBO="$(date +%Y%m%d-%H%M%S)"

mkdir -p "$DESTINO"

for banco in main.db users.db; do
  if [ ! -f "$ORIGEM/$banco" ]; then
    echo "[backup] $banco não existe em $ORIGEM (Postgres em uso?) — pulando."
    continue
  fi
  saida="$DESTINO/${banco%.db}-$CARIMBO.db"
  # `file:...?mode=ro` + immutable=0: leitura consistente sem travar escrita.
  sqlite3 "file:$ORIGEM/$banco?mode=ro" ".backup '$saida'"
  gzip -9 "$saida"
  echo "[backup] gerado $saida.gz"
done

# Verificação: um backup que ninguém testa é um backup que não existe.
ultimo="$(ls -1t "$DESTINO"/main-*.db.gz 2>/dev/null | head -n1 || true)"
if [ -n "$ultimo" ]; then
  gzip -dc "$ultimo" > /tmp/verifica.db
  resultado="$(sqlite3 /tmp/verifica.db 'PRAGMA integrity_check;')"
  rm -f /tmp/verifica.db
  if [ "$resultado" != "ok" ]; then
    echo "[backup] ALERTA: integrity_check falhou em $ultimo ($resultado)" >&2
    exit 1
  fi
  echo "[backup] integridade conferida em $ultimo"
fi

# Retenção.
find "$DESTINO" -name '*.db.gz' -mtime "+$RETENCAO_DIAS" -delete
echo "[backup] concluído ($(date -Iseconds))"

# ---------------------------------------------------------------------------
# RESTAURAÇÃO (procedimento manual, ~2 minutos)
#
#   docker compose stop wuzapi
#   gzip -dc backups/main-20260901-030000.db.gz  > /tmp/main.db
#   gzip -dc backups/users-20260901-030000.db.gz > /tmp/users.db
#   docker run --rm -v wuzapi_wuzapi-dbdata:/dbdata -v /tmp:/tmp alpine \
#     sh -c 'cp /tmp/main.db /tmp/users.db /dbdata/ && rm -f /dbdata/*.db-wal /dbdata/*.db-shm'
#   docker compose start wuzapi
#   curl -H "Token: $WUZAPI_TOKEN" http://127.0.0.1:8081/session/status
#     -> {"Connected":true,"LoggedIn":true} = sessão restaurada, sem QR.
#
# Apagar os -wal/-shm antigos é obrigatório: eles pertencem ao banco que foi
# substituído e, deixados para trás, o SQLite reaplica páginas de outro
# arquivo por cima da restauração.
#
# Se `LoggedIn` vier false, o pareamento foi revogado (alguém desconectou o
# aparelho no celular, ou o backup é anterior a um logout) — aí é QR novo.
# ---------------------------------------------------------------------------
