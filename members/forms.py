from django import forms
from django.forms import inlineformset_factory
from .models import CustomField, Guardian, Member


def build_custom_field_widgets(org, data=None, initial_values=None):
    """Return a list of dicts ready for template rendering."""
    fields = CustomField.objects.filter(organisation=org)
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
            'html': widget.render(key, current if cf.field_type != CustomField.FieldType.BOOLEAN else bool(current)),
            'is_bool': cf.field_type == CustomField.FieldType.BOOLEAN,
            'checked': bool(current),
        })
    return rendered


def extract_custom_field_values(org, post_data):
    """Pull custom field values from POST data and return the dict to store on Member."""
    fields = CustomField.objects.filter(organisation=org)
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
            'name', 'date_of_birth', 'email', 'phone',
            'emergency_contact_name', 'emergency_contact_phone',
            'emergency_contact_2_name', 'emergency_contact_2_phone',
            'joined_date', 'is_active', 'monthly_fee',
            'licence_number', 'licence_expiry',
            'medical_info',
        ]
        widgets = {
            'date_of_birth': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'joined_date': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'licence_expiry': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs['class'] = 'form-check-input'
            else:
                field.widget.attrs['class'] = 'form-control'
        self.fields['name'].widget.attrs['autofocus'] = True


class GuardianForm(forms.ModelForm):
    class Meta:
        model = Guardian
        fields = ['name', 'relationship', 'email', 'phone']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-control'
        self.fields['name'].required = False


GuardianFormSet = inlineformset_factory(
    Member, Guardian,
    form=GuardianForm,
    extra=1,
    can_delete=True,
)
