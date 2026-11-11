#!/bin/sh
set -eu
umask 077
python manage.py init
exec gunicorn wsgi:application --bind "0.0.0.0:${PORT:-10000}" --workers 2 --threads 2 --timeout 60 --limit-request-line 4094 --limit-request-fields 80 --error-logfile -
