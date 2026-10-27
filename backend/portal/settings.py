import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Load .env from D:\LAG_Portal\.env
load_dotenv(BASE_DIR.parent / ".env")
SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]
DEBUG=os.getenv('DJANGO_DEBUG','false').lower()=='true'
ALLOWED_HOSTS=os.getenv('DJANGO_ALLOWED_HOSTS','localhost,127.0.0.1,backend').split(',')
INSTALLED_APPS=['django.contrib.admin','django.contrib.auth','django.contrib.contenttypes','django.contrib.sessions','django.contrib.messages','django.contrib.staticfiles','rest_framework','rest_framework.authtoken','corsheaders','claims']
MIDDLEWARE=['django.middleware.security.SecurityMiddleware','corsheaders.middleware.CorsMiddleware','django.contrib.sessions.middleware.SessionMiddleware','django.middleware.common.CommonMiddleware','django.middleware.csrf.CsrfViewMiddleware','django.contrib.auth.middleware.AuthenticationMiddleware','django.contrib.messages.middleware.MessageMiddleware','django.middleware.clickjacking.XFrameOptionsMiddleware']
ROOT_URLCONF='portal.urls'
TEMPLATES=[{'BACKEND':'django.template.backends.django.DjangoTemplates','DIRS':[],'APP_DIRS':True,'OPTIONS':{'context_processors':['django.template.context_processors.debug','django.template.context_processors.request','django.contrib.auth.context_processors.auth','django.contrib.messages.context_processors.messages']}}]
WSGI_APPLICATION='portal.wsgi.application'
DATABASES={'default':{'ENGINE':'django.db.backends.postgresql','NAME':os.getenv('POSTGRES_DB','lagportal'),'USER':os.getenv('POSTGRES_USER','lagportal'),'PASSWORD':os.getenv('POSTGRES_PASSWORD','devpassword'),'HOST':os.getenv('POSTGRES_HOST','localhost'),'PORT':os.getenv('POSTGRES_PORT','5432')}}
AUTH_PASSWORD_VALIDATORS=[{'NAME':'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},{'NAME':'django.contrib.auth.password_validation.MinimumLengthValidator'},{'NAME':'django.contrib.auth.password_validation.CommonPasswordValidator'},{'NAME':'django.contrib.auth.password_validation.NumericPasswordValidator'}]
REST_FRAMEWORK={'DEFAULT_AUTHENTICATION_CLASSES':['rest_framework.authentication.TokenAuthentication','rest_framework.authentication.SessionAuthentication'],'DEFAULT_PERMISSION_CLASSES':['rest_framework.permissions.IsAuthenticated'],'DEFAULT_PAGINATION_CLASS':'rest_framework.pagination.PageNumberPagination','PAGE_SIZE':100}
CORS_ALLOWED_ORIGINS=os.getenv('CORS_ALLOWED_ORIGINS','http://localhost:5173').split(',')
CSRF_TRUSTED_ORIGINS=CORS_ALLOWED_ORIGINS
LANGUAGE_CODE='en-us';TIME_ZONE='UTC';USE_I18N=True;USE_TZ=True;STATIC_URL='static/';DEFAULT_AUTO_FIELD='django.db.models.BigAutoField'
MEDIA_ROOT=BASE_DIR/'protected-media';MEDIA_URL='/protected-media/'
SECURE_CONTENT_TYPE_NOSNIFF=True;SESSION_COOKIE_HTTPONLY=True;CSRF_COOKIE_HTTPONLY=False

EDI_ROOT = Path(
    os.getenv("EDI_ROOT", str(BASE_DIR.parent / "client-data"))
)
EDI_ALLOWED_ROOTS = [
    str(Path(x).resolve())
    for x in os.getenv("EDI_ALLOWED_ROOTS", str(EDI_ROOT)).split(os.pathsep)
    if x.strip()
]
