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

  # So a pasta /service-urls: tem o registry (URL e token) e o JWKS do api-auth, e nada de banco ou chaves de LLM.
  exec infisical run \
    --token="$INFISICAL_TOKEN" \
    --projectId="${INFISICAL_PROJECT_ID:-2296d19c-5f3b-41e1-afa3-fcde39966a71}" \
    --env="${INFISICAL_ENV:-qa}" \
    --path="${INFISICAL_PATH:-/service-urls}" \
    -- "$@"
fi

exec "$@"
