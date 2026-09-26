from django.db import migrations


class Migration(migrations.Migration):
    """
    Merge the two 0014 leaves: the member address fields (from upstream) and
    the portal leaving-request model. Both branch off 0013 and touch different
    tables, so there is nothing to reconcile — this just rejoins the graph.
    """

    dependencies = [
        ('members', '0014_member_address_line1_member_address_line2'),
        ('members', '0014_memberleavingrequest'),
    ]

    operations = []
