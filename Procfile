release: python manage.py migrate --noinput
web: gunicorn setup.wsgi:application --log-file -
