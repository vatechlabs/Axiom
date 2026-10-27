from datetime import date,timedelta
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from claims.models import Client,Membership,Claim,ErrorHistory
class Command(BaseCommand):
 help='Seed synthetic claims for demo; run ONLY in demo environments.'
 def handle(self,*args,**kwargs):
  User=get_user_model();u,created=User.objects.get_or_create(username='demo')
  if created:u.set_password('DemoOnly!ChangeMe2026');u.save()
  for code in ('XYZ','PQR'):
   client,_=Client.objects.get_or_create(code=code,defaults={'name':code+' Health Operations'})
   Membership.objects.get_or_create(user=u,client=client,defaults={'role':'admin'})
   errors=['Missing subscriber ID','Eligibility mismatch','','Invalid billing code','Provider ID mismatch','Coordination of benefits','','Duplicate submission','Missing payer identifier','Claim rejected']
   for i in range(24):
    age=(i*11+8)%125+1;err=errors[i%len(errors)]
    claim,_=Claim.objects.update_or_create(client=client,claim_number=f'{code}-CLM-{864291-i}',defaults={'dcn':f'DCN-{code}-{48130+i}','fund':['Alpha Fund','Pioneer Fund','Summit Fund','Heritage Fund'][i%4],'tpa':['Anthem','Aetna','Cigna','Humana'][i%4],'tpa_age':max(1,age-13),'total_age':age,'report_date':date.today()-timedelta(days=i%21),'error_message':err,'amount':5000+i*320})
    if err:
     ErrorHistory.objects.get_or_create(claim=claim,report_date=claim.report_date-timedelta(days=7),message='Pending verification')
     ErrorHistory.objects.get_or_create(claim=claim,report_date=claim.report_date,message=err)
  self.stdout.write(self.style.SUCCESS('Created XYZ/PQR synthetic demo claims. Credentials: demo / DemoOnly!ChangeMe2026'))
