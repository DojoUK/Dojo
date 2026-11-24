"""
The club dashboard's pages: the dashboard itself, saving its layout, and one widget at a time
for the Customise panel. The widgets and layouts live in organisations/dashboard.py.
"""
import json

from django.http import Http404, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.views import View
from django.views.generic import TemplateView

from dojo.mixins import OrgAdminMixin, OrgMixin

from . import dashboard


class DashboardView(OrgMixin, TemplateView):
    """The club's dashboard: the widgets on its layout, plus the getting-started checklist for new clubs."""
    template_name = 'org/dashboard_board.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        board = dashboard.Board(self.org, self.request.user, self.org_membership)
        layouts = dashboard.layouts_for(board)
        tablet = {i['key']: i for i in layouts['tablet']}
        widgets = []
        for item in sorted(layouts['wide'], key=lambda i: (i['y'], i['x'])):
            widget = dashboard.WIDGETS_BY_KEY[item['key']]
            context.update(dashboard.render_data(widget, board))
            widgets.append(frame_item(widget, item, tablet[item['key']]))

        single_session_setup = dashboard.single_session_setup(self.org) if board.is_admin else []
        context.update({
            'widgets': widgets,
            'dash_columns': dashboard.COLUMNS,
            'dash_sizes': {'wideFrom': dashboard.WIDE_FROM_PX, 'tabletFrom': dashboard.TABLET_FROM_PX,
                           'tabletColumns': dashboard.TABLET_COLUMNS},
            'today': board.today,
            'is_admin': board.is_admin,
            'can_customise': board.is_admin,
            'onboarding': self.onboarding(board, single_session_setup),
            'single_session_setup': single_session_setup,
        })
        if board.is_admin:
            context['dash_config'] = {
                'saveUrl': reverse('org_dashboard_layout', kwargs={'org_slug': self.org.slug}),
                'widgetUrl': reverse('org_dashboard_widget', kwargs={'org_slug': self.org.slug, 'key': '__key__'}),
                'categories': dashboard.CATEGORIES,
                'catalogue': dashboard.catalogue(board),
                'tabletCustom': layouts['tablet_custom'],
            }
        return context

    def onboarding(self, board, single_session_setup):
        """The getting-started checklist, until it's done or dismissed. Shown to admins."""
        if not board.is_admin or self.org.settings.get('onboarding_dismissed'):
            return None
        from billing.models import Invoice
        from classes.models import ClassCoach
        has_member = board.active_members.exists()
        has_class = self.org.classes.exists()
        has_coach = has_class and ClassCoach.objects.filter(assigned_class__organisation=self.org).exists()
        has_invoice = (
            not self.org.has_feature('billing')
            or Invoice.objects.filter(organisation=self.org).exists()
        )
        if has_member and has_class and has_coach and has_invoice and not single_session_setup:
            return None
        return {
            'has_member': has_member,
            'has_class': has_class,
            'has_coach': has_coach,
            'has_invoice': has_invoice,
            # Builds with class booking also need a single session price and Stripe.
            'booking_setup': self.org.has_feature('billing') and getattr(self.org, 'has_bookable_classes', False),
            'has_single_price': 'price' not in single_session_setup,
            'has_stripe': 'stripe' not in single_session_setup,
        }


def frame_item(widget, wide, tablet):
    """What org/widgets/_frame.html needs: the widget, its place at each size and its limits."""
    return {**wide, 'widget': widget, 'area': dashboard.grid_area(wide), 'tablet': tablet,
            'limits': widget.limits()}


class DashboardLayoutView(OrgAdminMixin, View):
    """Saves the club's dashboard layout (JSON {"widgets": [...]}), or resets it ({"reset": true})."""

    def post(self, request, org_slug):
        try:
            body = json.loads(request.body or b'{}')
        except ValueError:
            body = None
        if not isinstance(body, dict):
            return JsonResponse({'error': 'That layout could not be read.'}, status=400)
        board = dashboard.Board(self.org, request.user, self.org_membership)
        if body.get('reset'):
            dashboard.reset_layout(self.org)
            return JsonResponse({'widgets': dashboard.layout_for(board), 'reset': True})
        if not isinstance(body.get('widgets'), list):
            return JsonResponse({'error': 'That layout could not be read.'}, status=400)
        tablet = body.get('tablet') if isinstance(body.get('tablet'), list) else None
        dashboard.save_layout(self.org, body['widgets'], board, tablet)
        layouts = dashboard.layouts_for(board)
        return JsonResponse({'widgets': layouts['wide'], 'tablet': layouts['tablet'],
                             'tabletCustom': layouts['tablet_custom']})


class DashboardWidgetView(OrgAdminMixin, View):
    """One widget, ready to drop onto the grid while customising. ?x=&y=&w=&h= place it in the wide
    layout and ?tx=&ty=&tw=&th= in the tablet one."""

    def get(self, request, org_slug, key):
        board = dashboard.Board(self.org, request.user, self.org_membership)
        widget = dashboard.WIDGETS_BY_KEY.get(key)
        if widget is None or not widget.visible(board):
            raise Http404

        def number(name, default):
            try:
                return int(request.GET.get(name, default))
            except (TypeError, ValueError):
                return default

        def place(prefix, size, default_w):
            w, h = widget.clamp(number(prefix + 'w', default_w), number(prefix + 'h', widget.size[1]), size)
            return {'key': key, 'w': w, 'h': h,
                    'x': max(0, min(number(prefix + 'x', 0), dashboard.SIZE_COLUMNS[size] - w)),
                    'y': max(0, min(number(prefix + 'y', 0), dashboard.MAX_ROWS))}

        wide = place('', 'wide', widget.default_w())
        tablet = place('t', 'tablet', widget.default_w('tablet'))
        return render(request, 'org/widgets/_frame.html', {
            **dashboard.render_data(widget, board),
            'item': frame_item(widget, wide, tablet),
            'org': self.org,
            'org_membership': self.org_membership,
            'is_admin': board.is_admin,
            'can_customise': True,
            'today': board.today,
        })
