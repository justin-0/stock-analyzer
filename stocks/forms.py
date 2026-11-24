import re
from decimal import Decimal, InvalidOperation

from django import forms
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from .models import UserProfile

# (dial code, label, allowed national-number lengths, optional first-digit regex)
COUNTRY_CODES = [
    ('+91',  'India',          (10,),     r'[6-9]'),
    ('+971', 'UAE',            (9,),      None),
    ('+966', 'Saudi Arabia',   (9,),      None),
    ('+974', 'Qatar',          (8,),      None),
    ('+965', 'Kuwait',         (8,),      None),
    ('+968', 'Oman',           (8,),      None),
    ('+973', 'Bahrain',        (8,),      None),
    ('+1',   'USA / Canada',   (10,),     None),
    ('+44',  'United Kingdom', (10,),     None),
    ('+61',  'Australia',      (9,),      None),
    ('+65',  'Singapore',      (8,),      None),
    ('+60',  'Malaysia',       (9, 10),   None),
    ('+49',  'Germany',        (10, 11),  None),
]
COUNTRY_RULES = {code: (lengths, first) for code, _, lengths, first in COUNTRY_CODES}
COUNTRY_CHOICES = [(code, f'{label} ({code})') for code, label, _, _ in COUNTRY_CODES]

USERNAME_RE = re.compile(r'^[a-z][a-z0-9_]{2,19}$')
NAME_RE = re.compile(r"^[A-Za-z][A-Za-z .'-]*$")


def normalize_phone(country_code, number):
    """Validate a national number for the chosen country; return E.164 (+917034956971)."""
    if country_code not in COUNTRY_RULES:
        raise ValidationError('Pick a valid country code.')
    digits = re.sub(r'[\s\-()]', '', number or '')
    if digits.startswith('0'):
        digits = digits[1:]          # drop trunk prefix like 09876543210
    if not digits.isdigit():
        raise ValidationError('Phone number can contain digits only.')
    lengths, first = COUNTRY_RULES[country_code]
    if len(digits) not in lengths:
        want = ' or '.join(str(n) for n in lengths)
        raise ValidationError(f'Phone number must be {want} digits for {country_code}.')
    if first and not re.match(first, digits):
        raise ValidationError('Enter a valid mobile number (India numbers start with 6, 7, 8 or 9).')
    return country_code + digits


def phone_taken(e164, exclude_user=None):
    """True if any other profile already uses this number (also matches legacy
    numbers saved without the country code)."""
    national = e164[-10:]
    qs = UserProfile.objects.filter(phone__in=[e164, national])
    if exclude_user is not None:
        qs = qs.exclude(user=exclude_user)
    return qs.exists()


def email_taken(email, exclude_user=None):
    qs = User.objects.filter(email__iexact=email)
    if exclude_user is not None:
        qs = qs.exclude(pk=exclude_user.pk)
    return qs.exists()


def split_phone(e164):
    """'+917034956971' -> ('+91', '7034956971') for pre-filling forms."""
    for code, _, _, _ in sorted(COUNTRY_CODES, key=lambda c: -len(c[0])):
        if e164 and e164.startswith(code):
            return code, e164[len(code):]
    return '+91', e164 or ''


class RegisterForm(forms.Form):
    first_name = forms.CharField(max_length=30)
    last_name = forms.CharField(max_length=30)
    email = forms.EmailField(max_length=150)
    country_code = forms.ChoiceField(choices=COUNTRY_CHOICES, initial='+91')
    phone = forms.CharField(max_length=20)
    trading_experience = forms.ChoiceField(choices=UserProfile.EXPERIENCE_CHOICES)
    preferred_trade_type = forms.ChoiceField(choices=UserProfile.TRADE_TYPE_CHOICES)
    capital = forms.CharField(max_length=15)
    username = forms.CharField(max_length=60)   # length enforced by USERNAME_RE for a clearer message
    password = forms.CharField(widget=forms.PasswordInput)
    confirm_password = forms.CharField(widget=forms.PasswordInput)

    error_messages_required = 'This field is required.'

    def clean_first_name(self):
        v = self.cleaned_data['first_name'].strip()
        if not NAME_RE.match(v):
            raise ValidationError('Use letters only.')
        return v

    def clean_last_name(self):
        v = self.cleaned_data['last_name'].strip()
        if not NAME_RE.match(v):
            raise ValidationError('Use letters only.')
        return v

    def clean_username(self):
        v = self.cleaned_data['username'].strip().lower()   # always stored lowercase
        if not USERNAME_RE.match(v):
            raise ValidationError(
                'Username must start with a letter and use only small letters, '
                'numbers or _ (3–20 characters).'
            )
        if User.objects.filter(username__iexact=v).exists():
            raise ValidationError('This username is already taken.')
        return v

    def clean_email(self):
        v = self.cleaned_data['email'].strip().lower()
        if email_taken(v):
            raise ValidationError('This email is already registered.')
        return v

    def clean_capital(self):
        raw = self.cleaned_data['capital'].replace(',', '').strip()
        try:
            v = Decimal(raw)
        except InvalidOperation:
            raise ValidationError('Enter a number.')
        if v < 0 or v > Decimal('9999999999'):
            raise ValidationError('Enter an amount between 0 and 9,99,99,99,999.')
        return v.quantize(Decimal('0.01'))

    def clean(self):
        data = super().clean()

        code, number = data.get('country_code'), data.get('phone')
        if code and number:
            try:
                e164 = normalize_phone(code, number)
            except ValidationError as e:
                self.add_error('phone', e)
            else:
                if phone_taken(e164):
                    self.add_error('phone', 'This phone number is already registered.')
                else:
                    data['phone_e164'] = e164

        pw, confirm = data.get('password'), data.get('confirm_password')
        if pw and confirm and pw != confirm:
            self.add_error('confirm_password', 'Passwords do not match.')
        elif pw:
            probe = User(username=data.get('username', ''), email=data.get('email', ''),
                         first_name=data.get('first_name', ''), last_name=data.get('last_name', ''))
            try:
                validate_password(pw, user=probe)
            except ValidationError as e:
                self.add_error('password', e)
        return data
