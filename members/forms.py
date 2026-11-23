from django import forms
from django.forms import inlineformset_factory
from .models import CustomField, Guardian, Member


def build_custom_field_widgets(org, data=None, initial_values=None, fields=None):
    """Return a list of dicts ready for template rendering (all the club's fields unless `fields` is given)."""
    fields = CustomField.objects.filter(organisation=org) if fields is None else fields
    rendered = []
    for cf in fields:
        key = f'cf_{cf.pk}'
        current = (initial_values or {}).get(str(cf.pk), '')
        if cf.field_type == CustomField.FieldType.TEXT:
            widget = forms.TextInput(attrs={'class': 'form-control', 'name': key, 'value': current})
        elif cf.field_type == CustomField.FieldType.DATE:
            widget = forms.DateInput(attrs={'class': 'form-control', 'type': 'date', 'name': key, 'value': current})
        elif cf.field_type == CustomField.FieldType.BOOLEAN:
            widget = forms.CheckboxInput(attrs={'class': 'form-check-input', 'name': key})
        elif cf.field_type == CustomField.FieldType.SELECT:
            widget = forms.Select(
                choices=[('', '—')] + [(o, o) for o in cf.options],
                attrs={'class': 'form-select', 'name': key},
            )
        else:
            widget = forms.TextInput(attrs={'class': 'form-control', 'name': key})

        rendered.append({
            'field': cf,
            'key': key,
            'value': current,
            'html': widget.render(key, current if cf.field_type != CustomField.FieldType.BOOLEAN else bool(current)),
            'is_bool': cf.field_type == CustomField.FieldType.BOOLEAN,
            'checked': bool(current),
        })
    return rendered


def extract_custom_field_values(org, post_data, fields=None):
    """Pull custom field values from POST data and return the dict to store on Member."""
    fields = CustomField.objects.filter(organisation=org) if fields is None else fields
    values = {}
    for cf in fields:
        key = f'cf_{cf.pk}'
        if cf.field_type == CustomField.FieldType.BOOLEAN:
            values[str(cf.pk)] = key in post_data
        else:
            values[str(cf.pk)] = post_data.get(key, '')
    return values


class MemberForm(forms.ModelForm):
    class Meta:
        model = Member
        fields = [
            'first_name', 'surname', 'preferred_name', 'pronouns', 'date_of_birth', 'email', 'phone',
            'emergency_contact_name', 'emergency_contact_phone',
            'emergency_contact_2_name', 'emergency_contact_2_phone',
            'address_line1', 'address_line2', 'city', 'county', 'postcode',
            'joined_date', 'is_active', 'monthly_fee',
            'licence_number', 'licence_expiry',
            'medical_info', 'billing_policy',
        ]
        widgets = {
            'date_of_birth': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'joined_date': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'licence_expiry': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
        }

    def __init__(self, *args, org=None, **kwargs):
        # Anything still posting one full name (older pages, scripts) has it split into first name and surname.
        data = args[0] if args else kwargs.get('data')
        if data is not None and 'first_name' not in data and data.get('name'):
            from .names import split_name
            data = data.copy()
            data['first_name'], data['surname'] = split_name(data['name'])
            args = (data,) + tuple(args[1:]) if args else args
            if not args:
                kwargs['data'] = data
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs['class'] = 'form-check-input'
            else:
                field.widget.attrs['class'] = 'form-control'
        self.fields['first_name'].required = True
        self.fields['first_name'].widget.attrs.update(autofocus=True, autocomplete='off')
        self.fields['pronouns'].widget.attrs.update(list='pronounOptions', placeholder='e.g. she/her, he/him, they/them')
        self.fields['preferred_name'].widget.attrs['placeholder'] = 'If not their first name'
        if org:
            from organisations.optional_fields import MEMBER, disabled_model_fields
            for name in disabled_model_fields(org, MEMBER):
                del self.fields[name]
        # An account holder's own record: their Dojo account owns the shared details, so the club
        # sees them but doesn't edit them (they'd only be replaced next time the member saves).
        self.account_managed = bool(self.instance.pk and self.instance.is_account_holder)
        if self.account_managed:
            from network.details import SHARED_MEMBER_FIELDS
            for name in SHARED_MEMBER_FIELDS:
                if name in self.fields:
                    self.fields[name].disabled = True
                    self.fields[name].required = False
        if org and not org.has_feature('billing'):
            del self.fields['monthly_fee']
            del self.fields['billing_policy']
            return
        self.fields['billing_policy'].required = False
        self.fields['billing_policy'].empty_label = '— No policy —'
        if org:
            from billing.models import BillingPolicy
            self.fields['billing_policy'].queryset = BillingPolicy.objects.filter(
                organisation=org, is_active=True
            )


    def clean_billing_policy(self):
        policy = self.cleaned_data.get('billing_policy')
        if policy is not None and self.instance.pk and policy.pk != self.instance.billing_policy_id:
            from .family import plan_room_error
            error = plan_room_error(self.instance, policy)
            if error:
                raise forms.ValidationError(error + ' Take someone off the plan first.')
        return policy


class GuardianForm(forms.ModelForm):
    class Meta:
        model = Guardian
        fields = ['name', 'relationship', 'email', 'phone']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-control'
        self.fields['name'].required = False
        # A parent with a Dojo account: their name, email and phone follow it.
        self.account_managed = bool(self.instance.pk and self.instance.account_id)
        if self.account_managed:
            for name in ('name', 'email', 'phone'):
                self.fields[name].disabled = True


GuardianFormSet = inlineformset_factory(
    Member, Guardian,
    form=GuardianForm,
    extra=1,
    can_delete=True,
)
