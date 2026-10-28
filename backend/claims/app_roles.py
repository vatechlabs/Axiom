"""Axiom application roles. Main admin can manage users; demo reads LIVE client data."""
from django.contrib.auth.models import Group

DEMO_GROUP = 'axiom_demo'
MAIN_GROUP = 'axiom_main_superuser'

def is_demo(user):
    return bool(user.is_authenticated and (user.username.lower() == 'demo' or user.groups.filter(name=DEMO_GROUP).exists()))

def is_main(user):
    return bool(user.is_authenticated and user.is_superuser and user.groups.filter(name=MAIN_GROUP).exists())

def is_operator(user):
    return bool(user.is_authenticated and user.is_superuser and not is_demo(user))

def role_name(user):
    return 'demo' if is_demo(user) else 'main_superuser' if is_main(user) else 'superuser' if is_operator(user) else 'viewer'
