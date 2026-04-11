#!/usr/bin/env bash
# Exit on error
set -o errexit

# Install dependencies
pip install --upgrade pip
pip install -r requirements.txt

# Collect static files
python manage.py collectstatic --no-input

# Run database migrations (don't fail the build if migrations fail)
python manage.py migrate || true

# Create superuser automatically (don't fail the build if this fails)
echo "from django.contrib.auth import get_user_model; User = get_user_model(); User.objects.filter(email='krespresentsandcoffee@gmail.com').exists() or User.objects.create_superuser('admin', 'krespresentsandcoffee@gmail.com', 'january011821')" | python manage.py shell || true
