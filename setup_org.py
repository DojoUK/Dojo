from django.contrib.auth.models import User
from organisations.models import Organisation, OrganisationMember

# Create a default organisation
org, created = Organisation.objects.get_or_create(
    slug='default',
    defaults={'name': 'Default Organisation'}
)
print(f"Organisation: {org.name} (created={created})")

# Get the admin user
admin_user = User.objects.get(username='admin')

# Create OrganisationMember if not exists
org_member, created = OrganisationMember.objects.get_or_create(
    user=admin_user,
    organisation=org,
    defaults={'role': 'org_admin'}
)
print(f"OrganisationMember: {org_member} (created={created})")
print(f"Admin can now log in to organisation: {org.name}")
