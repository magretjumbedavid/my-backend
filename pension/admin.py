from django.contrib import admin
from .models import PensionProvider, PensionAccount

admin.site.register(PensionProvider)
admin.site.register(PensionAccount)
