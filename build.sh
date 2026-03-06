#!/usr/bin/env bash
# Exit on error
set -o errexit

# Install dependencies
pip install --upgrade pip
pip install -r requirements.txt

# Collect static files
python manage.py collectstatic --no-input

# Run database migrations
python manage.py migrate

# Create superuser automatically (skips if already exists)
echo "from django.contrib.auth import get_user_model; User = get_user_model(); User.objects.filter(username='kres_admin').exists() or User.objects.create_superuser('kres_admin', 'naca.concepcion.coc@phinmaed.com', 'KresAdmin20')" | python manage.py shell
