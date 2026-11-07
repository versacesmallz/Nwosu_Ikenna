#!/usr/bin/env bash
# =====================================================================
# MIT 8103 - Portfolio 5 : build a 4-node PostgreSQL 16 topology on one host
#
#   node        port  role
#   primary     5432  read/write primary (the kasuwa DB from Portfolios 1-4) + shard coordinator
#   replica1    5433  streaming-replication hot standby of primary (read-only)
#   shard_south 5434  holds orders of the SE, SS, SW geopolitical zones
#   shard_north 5435  holds orders of the NC, NE, NW geopolitical zones
#
# Each node is a separate postgres server process with its own data directory,
# WAL and port - the same as separate VMs, minus network latency.
# Run as root:  bash 01_setup_cluster.sh
# =====================================================================
set -euo pipefail
PGBIN=/usr/lib/postgresql/16/bin
BASE=/home/claude/pg
as_pg() { su postgres -c "$*"; }

echo "== primary: enable replication settings and restart"
as_pg "$PGBIN/pg_ctl -D $BASE/primary -o \"-p 5432 -c wal_level=replica -c max_wal_senders=5 \
  -c wal_keep_size=256MB -c hot_standby=on -c listen_addresses=localhost\" -l $BASE/primary.log -w restart"
psql -h localhost -p 5432 -U postgres -qc "SELECT pg_drop_replication_slot('replica1_slot')" 2>/dev/null || true
psql -h localhost -p 5432 -U postgres -qc "SELECT pg_create_physical_replication_slot('replica1_slot')"

echo "== replica1: base backup of primary with -R (writes standby.signal + primary_conninfo)"
as_pg "$PGBIN/pg_ctl -D $BASE/replica1 -m fast stop" 2>/dev/null || true
rm -rf $BASE/replica1
as_pg "$PGBIN/pg_basebackup -h localhost -p 5432 -U postgres -D $BASE/replica1 \
       -X stream -S replica1_slot -R --checkpoint=fast --progress"
echo "cluster_name = 'replica1'" >> $BASE/replica1/postgresql.auto.conf
sed -i "s/primary_conninfo = '/primary_conninfo = 'application_name=replica1 /" $BASE/replica1/postgresql.auto.conf
as_pg "$PGBIN/pg_ctl -D $BASE/replica1 -o '-p 5433' -l $BASE/replica1.log -w start"

for SHARD in shard_south:5434 shard_north:5435; do
  NAME=${SHARD%%:*}; PORT=${SHARD##*:}
  echo "== $NAME on port $PORT"
  as_pg "$PGBIN/pg_ctl -D $BASE/$NAME -m fast stop" 2>/dev/null || true
  rm -rf $BASE/$NAME
  as_pg "$PGBIN/initdb -D $BASE/$NAME -A trust -U postgres" > /dev/null
  as_pg "$PGBIN/pg_ctl -D $BASE/$NAME -o '-p $PORT -c listen_addresses=localhost' -l $BASE/$NAME.log -w start"
  psql -h localhost -p $PORT -U postgres -qc "CREATE DATABASE kasuwa_shard"
done

echo "== cluster status"
for P in 5432 5433 5434 5435; do
  printf "port %s : " $P
  psql -h localhost -p $P -U postgres -Atc "SELECT CASE WHEN pg_is_in_recovery() THEN 'standby (read-only)' ELSE 'primary (read/write)' END || ', ' || current_setting('data_directory')"
done
