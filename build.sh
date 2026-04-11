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
echo "from django.contrib.auth import get_user_model; User = get_user_model(); User.objects.filter(email='krespresentsandcoffee@gmail.com').exists() or User.objects.create_superuser('admin', 'krespresentsandcoffee@gmail.com', 'january011821')" | python manage.py shell
