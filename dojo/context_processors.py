from django.conf import settings


def dojo_licence(request):
    key = getattr(settings, 'DOJO_LICENCE_KEY', '')
    holder = getattr(settings, 'DOJO_LICENCE_HOLDER', '')
    if key and holder:
        licence = {'type': 'commercial', 'holder': holder}
    else:
        licence = {'type': 'agpl'}
    return {'dojo_licence': licence}


def dojo_version(request):
    """Which Dojo is running, for the sidebar footer. Read once per process."""
    from .version import get_version_info
    return {'dojo_version': get_version_info()}


def dojo_debug(request):
    return {'DOJO_DEBUG': settings.DEBUG}
