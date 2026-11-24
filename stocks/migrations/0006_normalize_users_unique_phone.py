import re

from django.db import migrations, models


def normalize(apps, schema_editor):
    User = apps.get_model('auth', 'User')
    UserProfile = apps.get_model('stocks', 'UserProfile')

    # Usernames are now always lowercase (e.g. Justin001 -> justin001).
    for u in User.objects.all():
        low = u.username.lower()
        if low != u.username and not User.objects.filter(username=low).exclude(pk=u.pk).exists():
            u.username = low
            u.save(update_fields=['username'])

    # Emails lowercase, unless that would collide with another account.
    for u in User.objects.exclude(email=''):
        low = u.email.strip().lower()
        if low != u.email and not User.objects.filter(email__iexact=low).exclude(pk=u.pk).exists():
            u.email = low
            u.save(update_fields=['email'])

    # Phones to E.164. Legacy 10-digit numbers were Indian mobiles -> +91.
    seen = set()
    for p in UserProfile.objects.exclude(phone='').order_by('id'):
        raw = p.phone.strip()
        digits = re.sub(r'\D', '', raw)
        if raw.startswith('+'):
            new = '+' + digits
        elif len(digits) == 10:
            new = '+91' + digits
        elif len(digits) == 12 and digits.startswith('91'):
            new = '+' + digits
        else:
            new = raw
        if new in seen:
            new = ''            # duplicate: keep the oldest account's number only
        seen.add(new)
        if new != p.phone:
            p.phone = new
            p.save(update_fields=['phone'])


class Migration(migrations.Migration):

    dependencies = [
        ('stocks', '0005_papertrade'),
        ('auth', '0012_alter_user_first_name_max_length'),
    ]

    operations = [
        migrations.RunPython(normalize, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name='userprofile',
            constraint=models.UniqueConstraint(
                condition=models.Q(('phone', ''), _negated=True),
                fields=('phone',), name='unique_nonempty_phone',
            ),
        ),
    ]
