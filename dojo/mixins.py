from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.http import Http404
from organisations.models import Organisation, OrganisationMember


def _resolve_org_membership(view, request, kwargs):
    """Resolve org and membership onto the view instance. Idempotent."""
    if hasattr(view, '_org_resolved'):
        return

    try:
        view.org = Organisation.objects.get(slug=kwargs['org_slug'])
    except Organisation.DoesNotExist:
        raise PermissionDenied

    if request.user.is_superuser:
        view.org_membership = None
    else:
        try:
            view.org_membership = OrganisationMember.objects.get(
                user=request.user, organisation=view.org
            )
        except OrganisationMember.DoesNotExist:
            raise PermissionDenied

    view._org_resolved = True


def has_capability(org_membership, capability):
    """
    Whether the resolved membership holds a staff capability (see
    OrganisationMember.CAPABILITIES). Superusers resolve to
    org_membership=None and hold every capability.
    """
    return org_membership is None or org_membership.has_capability(capability)


def require_features(org, features):
    """404 unless every feature key in `features` is switched on for the org."""
    if isinstance(features, str):
        features = (features,)
    if not all(org.has_feature(f) for f in features):
        raise Http404


class OrgMixin(LoginRequiredMixin):
    """
    Grants access to any member of the organisation in the URL.
    Sets self.org and self.org_membership on the view.
    URL must include an org_slug kwarg.

    Set `required_feature` (a feature key, or a tuple of keys) to 404 the
    view when the organisation has that optional feature switched off.
    """
    required_feature = None

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        _resolve_org_membership(self, request, kwargs)
        if self.required_feature:
            require_features(self.org, self.required_feature)
        return super().dispatch(request, *args, **kwargs)

    @property
    def is_org_admin(self):
        return self.org_membership is None or self.org_membership.is_org_admin

    def can(self, capability):
        return has_capability(self.org_membership, capability)

    def coached_class_ids(self):
        """Ids of the classes in this org the current user is assigned to coach."""
        from classes.models import ClassCoach
        return ClassCoach.objects.filter(
            user=self.request.user, assigned_class__organisation=self.org,
        ).values_list('assigned_class_id', flat=True)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['org'] = self.org
        context['org_membership'] = self.org_membership
        return context


class OrgAdminMixin(OrgMixin):
    """
    Restricts access to Org Admins (and superusers).
    Coaches hitting an admin-only view get a 403.
    """

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        _resolve_org_membership(self, request, kwargs)
        if self.org_membership and self.org_membership.role != OrganisationMember.Role.ORG_ADMIN:
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)


class CapabilityMixin(OrgMixin):
    """
    Restricts access to Org Admins (and superusers) plus any staff member
    granted at least one of `required_capabilities`.
    """
    required_capabilities = ()

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        _resolve_org_membership(self, request, kwargs)
        if not any(self.can(c) for c in self.required_capabilities):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)


class ClassCoachMixin(OrgMixin):
    """
    For class-specific views. Org Admins can access any class.
    Coaches can only access classes they are assigned to.
    Sets self.assigned_class on the view.
    URL must include a class_pk kwarg (or pk for class detail views).
    """

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        _resolve_org_membership(self, request, kwargs)

        from classes.models import Class, ClassCoach
        class_pk = kwargs.get('class_pk') or kwargs.get('pk')
        try:
            self.assigned_class = Class.objects.get(pk=class_pk, organisation=self.org)
        except Class.DoesNotExist:
            raise PermissionDenied

        if self.org_membership and self.org_membership.role != OrganisationMember.Role.ORG_ADMIN:
            if not ClassCoach.objects.filter(
                assigned_class=self.assigned_class, user=request.user
            ).exists():
                raise PermissionDenied

        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['assigned_class'] = self.assigned_class
        return context
