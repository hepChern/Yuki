#!/bin/bash
echo "New environment"
celery -q --workdir=/Users/zhaomr/workdir/Chern/Yuki/Yuki/.. -A Yuki.server.celeryapp worker --loglevel=info
