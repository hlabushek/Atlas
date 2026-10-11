#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
backup=/opt/atlas/backups/encrypted/atlas-20261003T133605Z.dump.gpg
[[ -f "$backup" ]] || exit 1
suffix=$(od -An -N8 -tx1 /dev/urandom | tr -d ' \n')
database="atlas_voice_drill_$suffix"
[[ "$database" =~ ^atlas_voice_drill_[a-f0-9]{16}$ ]] || exit 1
created=0
cleanup() { if (( created )); then docker exec atlas-postgres-1 dropdb -U atlas --if-exists "$database" >/dev/null; fi; }
trap cleanup EXIT
docker exec atlas-postgres-1 createdb -U atlas "$database"
created=1
gpg --batch --pinentry-mode loopback --passphrase-file /etc/atlas-backup/passphrase --decrypt "$backup" | docker exec -i atlas-postgres-1 pg_restore -U atlas -d "$database" --exit-on-error --no-owner
docker run --rm --network atlas_default --env-file /opt/atlas/app/.env --env PYTHONPATH=/app --mount type=bind,src=/opt/atlas/staging/audit-demo-replies.py,dst=/run/audit-demo-replies.py,readonly --entrypoint python atlas-api:chat-settings-20261003 /run/audit-demo-replies.py "$database"
