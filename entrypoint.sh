#!/bin/sh
set -e

if [ "$#" -eq 0 ]; then
  echo "entrypoint.sh requires an application command" >&2
  exit 64
fi

if [ -n "${INFISICAL_CLIENT_ID:-}" ] && [ -n "${INFISICAL_CLIENT_SECRET:-}" ]; then
  INFISICAL_TOKEN=$(infisical login --method=universal-auth \
    --client-id="$INFISICAL_CLIENT_ID" \
    --client-secret="$INFISICAL_CLIENT_SECRET" \
    --silent --plain)

  # Uma execução por pasta: chaves dos provedores, URLs dos serviços e chaves dos chamadores, nada de banco.
  FLAGS="--token=$INFISICAL_TOKEN --projectId=${INFISICAL_PROJECT_ID:-2296d19c-5f3b-41e1-afa3-fcde39966a71} --env=${INFISICAL_ENV:-qa}"

  exec infisical run $FLAGS --path=/llm -- \
    infisical run $FLAGS --path=/service-urls -- \
    infisical run $FLAGS --path=/service-keys -- \
    "$@"
fi

exec "$@"
