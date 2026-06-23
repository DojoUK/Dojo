from django.conf import settings


def dojo_licence(request):
    key = getattr(settings, 'DOJO_LICENCE_KEY', '')
    holder = getattr(settings, 'DOJO_LICENCE_HOLDER', '')
    if key and holder:
        licence = {'type': 'commercial', 'holder': holder}
    else:
        licence = {'type': 'agpl'}
    return {'dojo_licence': licence}
