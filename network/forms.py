from django import forms
from django.contrib.auth import password_validation
from django.contrib.auth.forms import AuthenticationForm

from .accounts import email_taken, normalise_email


class SignUpForm(forms.Form):
    """
    A new Dojo account, with everything My details holds, so joining a club afterwards is a matter
    of checking a form that's already filled in. The details needed to train safely are required.
    """
    first_name = forms.CharField(max_length=150, label='First name')
    surname = forms.CharField(max_length=150, label='Surname')
    email = forms.EmailField(label='Email')
    password = forms.CharField(widget=forms.PasswordInput, label='Password')
    password_confirm = forms.CharField(widget=forms.PasswordInput, label='Confirm password')

    preferred_name = forms.CharField(max_length=150, required=False, label='Preferred name')
    pronouns = forms.CharField(max_length=30, required=False, label='Pronouns')
    date_of_birth = forms.DateField(label='Date of birth', widget=forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'))
    phone = forms.CharField(max_length=20, label='Phone')

    address_line1 = forms.CharField(max_length=255, label='Address')
    address_line2 = forms.CharField(max_length=255, required=False, label='Second line')
    city = forms.CharField(max_length=100, label='Town or city')
    county = forms.CharField(max_length=100, required=False, label='County')
    postcode = forms.CharField(max_length=20, label='Postcode')

    emergency_contact_name = forms.CharField(max_length=255, label='Name')
    emergency_contact_phone = forms.CharField(max_length=20, label='Phone')
    emergency_contact_2_name = forms.CharField(max_length=255, required=False, label='Second contact: name')
    emergency_contact_2_phone = forms.CharField(max_length=20, required=False, label='Second contact: phone')

    medical_info = forms.CharField(required=False, label='Conditions, allergies, or anything coaches should know',
                                   widget=forms.Textarea(attrs={'rows': 3}))

    guardian_name = forms.CharField(max_length=255, required=False, label='Name')
    guardian_email = forms.EmailField(required=False, label='Email')
    guardian_phone = forms.CharField(max_length=20, required=False, label='Phone')

    # (title, note, fields) for the page; the account's own fields come first.
    GROUPS = [
        ('Your account', '', ['first_name', 'surname', 'email', 'password', 'password_confirm']),
        ('About you', 'Your preferred name and pronouns show on class registers.',
         ['preferred_name', 'pronouns', 'date_of_birth', 'phone']),
        ('Address', '', ['address_line1', 'address_line2', 'city', 'county', 'postcode']),
        ('Emergency contacts', 'Who a club should call if something happens to you while training.',
         ['emergency_contact_name', 'emergency_contact_phone', 'emergency_contact_2_name', 'emergency_contact_2_phone']),
        ('Medical information', 'Clubs that ask for it show it to coaches on their registers. Leave blank if there\'s nothing.',
         ['medical_info']),
        ('Parent or guardian', 'Only needed if you\'re under 18.', ['guardian_name', 'guardian_email', 'guardian_phone']),
    ]
    # Everything after the account's own fields is saved to My details.
    DETAIL_FIELDS = [name for _, _, names in GROUPS[1:] for name in names]
    AUTOCOMPLETE = {
        'first_name': 'given-name', 'surname': 'family-name', 'email': 'email', 'password': 'new-password',
        'password_confirm': 'new-password', 'date_of_birth': 'bday', 'phone': 'tel', 'address_line1': 'address-line1',
        'address_line2': 'address-line2', 'city': 'address-level2', 'county': 'address-level1',
        'postcode': 'postal-code', 'emergency_contact_phone': 'off', 'guardian_email': 'off',
    }
    PLACEHOLDERS = {'preferred_name': 'If not your first name', 'pronouns': 'e.g. she/her, he/him, they/them'}
    WIDE = {'address_line1', 'address_line2', 'medical_info'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            attrs = field.widget.attrs
            attrs['class'] = 'form-control'
            attrs['autocomplete'] = self.AUTOCOMPLETE.get(name, 'off')
            if name in self.PLACEHOLDERS:
                attrs['placeholder'] = self.PLACEHOLDERS[name]
            if name.endswith('phone'):
                attrs['type'] = 'tel'
        self.fields['pronouns'].widget.attrs['list'] = 'pronoun-options'

    def groups(self):
        if self.is_bound:
            for name in self.errors:
                if name in self.fields:
                    self.fields[name].widget.attrs['class'] = 'form-control is-invalid'
        return [{'title': title, 'note': note,
                 'fields': [{'field': self[name], 'wide': name in self.WIDE} for name in names]}
                for title, note, names in self.GROUPS]

    def clean_email(self):
        email = normalise_email(self.cleaned_data['email'])
        if email_taken(email):
            raise forms.ValidationError('There\'s already a Dojo account with that email. Sign in, or reset your password.')
        return email

    def clean_date_of_birth(self):
        from datetime import date
        born = self.cleaned_data['date_of_birth']
        today = date.today()
        if born > today:
            raise forms.ValidationError('Your date of birth can\'t be in the future.')
        if born.year < today.year - 120:
            raise forms.ValidationError('Check the year of your date of birth.')
        return born

    def clean(self):
        data = super().clean()
        password, confirm = data.get('password'), data.get('password_confirm')
        if password and confirm and password != confirm:
            self.add_error('password_confirm', 'The passwords don\'t match.')
        elif password:
            try:
                password_validation.validate_password(password)
            except forms.ValidationError as e:
                self.add_error('password', e)
        if bool(data.get('emergency_contact_2_name')) != bool(data.get('emergency_contact_2_phone')):
            missing = 'emergency_contact_2_phone' if data.get('emergency_contact_2_name') else 'emergency_contact_2_name'
            self.add_error(missing, 'Give both a name and a phone number for the second contact, or neither.')
        return data


class SignInForm(AuthenticationForm):
    """
    Sign in with an email address. Accounts made on the member side use their email as the
    username, but a member account can also sit on a staff sign-in (network/accounts.py
    account_for), whose username is something else: find the sign-in by its email too.
    """
    username = forms.EmailField(label='Email', widget=forms.EmailInput(attrs={'autofocus': True, 'autocomplete': 'email'}))

    def clean_username(self):
        from django.contrib.auth.models import User
        email = normalise_email(self.cleaned_data['username'])
        by_username = User.objects.filter(username__iexact=email).first()
        if by_username:
            return by_username.username
        by_email = list(User.objects.filter(email__iexact=email)[:2])
        return by_email[0].username if len(by_email) == 1 else email
