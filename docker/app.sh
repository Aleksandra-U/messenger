#!/bin/bash

alembic upgrade head

# Правильный синтаксис для запуска gunicorn
gunicorn -k uvicorn.workers.UvicornWorker app.main:app --bind 0.0.0.0:8000