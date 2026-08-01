from django.contrib import messages
from django.db.models import ProtectedError
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from dojo.mixins import OrgAdminMixin

from .models import Product, ProductVariant, StockMovement


class ProductListView(OrgAdminMixin, View):
    template_name = 'inventory/list.html'

    def get(self, request, org_slug):
        products = (
            Product.objects.filter(organisation=self.org)
            .prefetch_related('variants')
            .order_by('name')
        )
        low_stock_variants = [
            v for v in ProductVariant.objects.filter(product__organisation=self.org, is_active=True)
            if v.is_low_stock
        ]
        return render(request, self.template_name, {
            'org': self.org,
            'org_membership': self.org_membership,
            'products': products,
            'categories': Product.Category.choices,
            'reasons': StockMovement.Reason.choices,
            'low_stock_variants': low_stock_variants,
        })


class ProductCreateView(OrgAdminMixin, View):
    def post(self, request, org_slug):
        name = request.POST.get('name', '').strip()
        category = request.POST.get('category', Product.Category.OTHER)
        description = request.POST.get('description', '').strip()

        if not name:
            messages.error(request, 'Product name is required.')
            return redirect('inventory_list', org_slug=self.org.slug)

        Product.objects.create(
            organisation=self.org,
            name=name,
            category=category,
            description=description,
        )
        messages.success(request, f'Product "{name}" created. Now add its sizes and prices.')
        return redirect('inventory_list', org_slug=self.org.slug)


class ProductEditView(OrgAdminMixin, View):
    def post(self, request, org_slug, pk):
        product = get_object_or_404(Product, pk=pk, organisation=self.org)
        product.name = request.POST.get('name', product.name).strip()
        product.category = request.POST.get('category', product.category)
        product.description = request.POST.get('description', '').strip()
        product.is_active = request.POST.get('is_active') == '1'
        product.save()
        messages.success(request, f'Product "{product.name}" updated.')
        return redirect('inventory_list', org_slug=self.org.slug)


class ProductDeleteView(OrgAdminMixin, View):
    def post(self, request, org_slug, pk):
        product = get_object_or_404(Product, pk=pk, organisation=self.org)
        name = product.name
        try:
            product.delete()
            messages.success(request, f'Product "{name}" deleted.')
        except ProtectedError:
            messages.error(
                request,
                f'Can\'t delete "{name}" — it has been sold on at least one invoice. '
                'Mark it inactive instead to hide it from new invoices.',
            )
        return redirect('inventory_list', org_slug=self.org.slug)


class VariantCreateView(OrgAdminMixin, View):
    def post(self, request, org_slug, product_pk):
        product = get_object_or_404(Product, pk=product_pk, organisation=self.org)
        size = request.POST.get('size', '').strip()
        sku = request.POST.get('sku', '').strip()
        price = request.POST.get('price', '').strip()
        quantity = request.POST.get('quantity_in_stock', '0').strip()
        low_stock_threshold = request.POST.get('low_stock_threshold', '0').strip()

        if not size or not price:
            messages.error(request, 'Size and price are required.')
            return redirect('inventory_list', org_slug=self.org.slug)

        try:
            price_val = float(price)
            quantity_val = int(quantity or 0)
            threshold_val = int(low_stock_threshold or 0)
        except ValueError:
            messages.error(request, 'Price, quantity, and low-stock threshold must be numbers.')
            return redirect('inventory_list', org_slug=self.org.slug)

        if price_val < 0 or quantity_val < 0 or threshold_val < 0:
            messages.error(request, 'Price, quantity, and low-stock threshold can\'t be negative.')
            return redirect('inventory_list', org_slug=self.org.slug)

        if ProductVariant.objects.filter(product=product, size__iexact=size).exists():
            messages.error(request, f'{product.name} already has a size "{size}".')
            return redirect('inventory_list', org_slug=self.org.slug)

        variant = ProductVariant.objects.create(
            product=product,
            size=size,
            sku=sku,
            price=price_val,
            quantity_in_stock=quantity_val,
            low_stock_threshold=threshold_val,
        )
        if quantity_val > 0:
            StockMovement.objects.create(
                variant=variant,
                quantity_change=quantity_val,
                reason=StockMovement.Reason.RESTOCK,
                notes='Initial stock on creation',
                created_by=request.user if request.user.is_authenticated else None,
            )
        messages.success(request, f'Added size "{size}" to {product.name}.')
        return redirect('inventory_list', org_slug=self.org.slug)


class VariantEditView(OrgAdminMixin, View):
    """Edits the catalogue details of a variant. Stock levels are changed only via VariantAdjustStockView
    so every change to quantity_in_stock is logged as a StockMovement."""

    def post(self, request, org_slug, pk):
        variant = get_object_or_404(ProductVariant, pk=pk, product__organisation=self.org)
        variant.size = request.POST.get('size', variant.size).strip()
        variant.sku = request.POST.get('sku', '').strip()
        variant.is_active = request.POST.get('is_active') == '1'
        try:
            variant.price = float(request.POST.get('price', variant.price))
            variant.low_stock_threshold = int(request.POST.get('low_stock_threshold', variant.low_stock_threshold) or 0)
        except ValueError:
            messages.error(request, 'Invalid price or low-stock threshold.')
            return redirect('inventory_list', org_slug=self.org.slug)

        if variant.price < 0 or variant.low_stock_threshold < 0:
            messages.error(request, 'Price and low-stock threshold can\'t be negative.')
            return redirect('inventory_list', org_slug=self.org.slug)

        variant.save()
        messages.success(request, f'Updated {variant}.')
        return redirect('inventory_list', org_slug=self.org.slug)


class VariantDeleteView(OrgAdminMixin, View):
    def post(self, request, org_slug, pk):
        variant = get_object_or_404(ProductVariant, pk=pk, product__organisation=self.org)
        label = str(variant)
        try:
            variant.delete()
            messages.success(request, f'Deleted {label}.')
        except ProtectedError:
            messages.error(
                request,
                f'Can\'t delete {label} — it has been sold on at least one invoice. '
                'Mark it inactive instead to hide it from new invoices.',
            )
        return redirect('inventory_list', org_slug=self.org.slug)


class VariantAdjustStockView(OrgAdminMixin, View):
    """Restock, correct, or write off stock for one variant. Always goes through
    ProductVariant.adjust_stock() so the change is locked and logged."""

    def post(self, request, org_slug, pk):
        variant = get_object_or_404(ProductVariant, pk=pk, product__organisation=self.org)
        delta_raw = request.POST.get('delta', '').strip()
        reason = request.POST.get('reason', StockMovement.Reason.CORRECTION)
        notes = request.POST.get('notes', '').strip()

        try:
            delta = int(delta_raw)
        except (TypeError, ValueError):
            messages.error(request, 'Enter a whole number to adjust stock by (e.g. 10 or -2).')
            return redirect('inventory_list', org_slug=self.org.slug)

        if delta == 0:
            messages.error(request, 'Enter a non-zero adjustment.')
            return redirect('inventory_list', org_slug=self.org.slug)

        try:
            variant.adjust_stock(delta, reason=reason, notes=notes, user=request.user)
        except ValueError as e:
            messages.error(request, str(e))
            return redirect('inventory_list', org_slug=self.org.slug)

        sign = '+' if delta > 0 else ''
        messages.success(request, f'{variant}: stock adjusted {sign}{delta} (now {variant.quantity_in_stock}).')
        return redirect('inventory_list', org_slug=self.org.slug)
