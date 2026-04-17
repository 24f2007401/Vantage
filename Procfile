web: gunicorn "app:create_app()" --config gunicorn.conf.py
web: flask db upgrade && gunicorn app:create_app --config gunicorn.conf.py