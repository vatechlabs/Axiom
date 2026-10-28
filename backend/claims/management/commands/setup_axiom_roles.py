import os
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand, CommandError
from rest_framework.authtoken.models import Token

class Command(BaseCommand):
    help='Designate existing main superuser and provision/update the public demo account (live data, read-only).'
    def add_arguments(self,p):
        p.add_argument('--main',required=True,help='Username of existing Django superuser')
    def handle(self,*args,**opts):
        User=get_user_model()
        try:main=User.objects.get(username=opts['main'],is_superuser=True,is_active=True)
        except User.DoesNotExist:raise CommandError('Main account must already exist and be an active Django superuser')
        main_group,_=Group.objects.get_or_create(name='axiom_main_superuser')
        demo_group,_=Group.objects.get_or_create(name='axiom_demo')
        main.groups.add(main_group)
        password=os.getenv('AXIOM_DEMO_PASSWORD','')
        if not password:raise CommandError('Set AXIOM_DEMO_PASSWORD environment variable before running this command')
        demo,_=User.objects.get_or_create(username='demo')
        demo.set_password(password)
        demo.is_active=True;demo.is_staff=False;demo.is_superuser=False
        demo.save()
        demo.groups.add(demo_group)
        Token.objects.filter(user=demo).delete()
        self.stdout.write(self.style.SUCCESS('Main admin and demo account configured. Demo sees all live clients but cannot mutate data.'))
