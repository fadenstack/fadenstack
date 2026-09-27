#!/bin/sh
# Fadenstack -- choose the nginx server config at container start.
#
# HTTPS when the edge material is in place (edge.crt and edge.key under
# /etc/faden/tls, the install's ./tls directory), plain HTTP otherwise.
# The container command runs this before nginx itself, so restarting the
# container is all it takes to switch either way; the backend does that
# when material is activated or removed through /api/admin/tls.
set -eu

SRC=/etc/nginx/faden
TLS=/etc/faden/tls
OUT=/etc/nginx/conf.d/default.conf
CHAIN_DIR=/etc/nginx/faden-tls

if [ -s "$TLS/edge.crt" ] && [ -s "$TLS/edge.key" ]; then
    # nginx wants the leaf and its chain in one file; the backend writes them apart.
    mkdir -p "$CHAIN_DIR"
    cat "$TLS/edge.crt" > "$CHAIN_DIR/fullchain.pem"
    if [ -n "$(tail -c 1 "$CHAIN_DIR/fullchain.pem")" ]; then
        echo >> "$CHAIN_DIR/fullchain.pem"
    fi
    if [ -s "$TLS/edge-chain.pem" ]; then
        cat "$TLS/edge-chain.pem" >> "$CHAIN_DIR/fullchain.pem"
    fi
    chmod 644 "$CHAIN_DIR/fullchain.pem"

    port="${FADEN_HTTPS_PORT:-443}"
    if [ "$port" = "443" ]; then suffix=""; else suffix=":$port"; fi
    sed -e "s|__HTTPS_PORT_SUFFIX__|$suffix|g" \
        -e "s|__HSTS_MAX_AGE__|${FADEN_HSTS_MAX_AGE:-0}|g" \
        "$SRC/nginx-tls.conf" > "$OUT"
    echo "Fadenstack nginx: HTTPS on, edge material found in $TLS"
else
    cp "$SRC/nginx.conf" "$OUT"
    echo "Fadenstack nginx: plain HTTP, no edge material in $TLS"
fi
