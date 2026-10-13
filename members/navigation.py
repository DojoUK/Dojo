from django.shortcuts import redirect
from django.urls import reverse


def redirect_to_member(org_slug, member_pk, tab):
    """Back to a member's page, opened on the given tab (overview, grades, billing, documents, notes)."""
    return redirect(f"{reverse('member_detail', kwargs={'org_slug': org_slug, 'pk': member_pk})}#{tab}")
