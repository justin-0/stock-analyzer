import json
import os
import re
import subprocess
import sys
from pathlib import Path
from unittest import mock

from django.contrib.auth.models import User
from django.core import mail
from django.test import Client, TestCase

from .models import PaperTrade, UserProfile
from .utils import generate_signal


def make_user(username, password='Str0ng-pass-123', email=None, **extra):
    is_admin = extra.pop('is_admin', False)
    user = User.objects.create_user(
        username=username, password=password,
        email=email or f'{username}@example.com', **extra,
    )
    UserProfile.objects.create(user=user, is_admin=is_admin)
    return user


BASE_IND = dict(
    current_price=100, rsi=50, macd_diff=0, ema9=100, ema21=100,
    sma50=100, sma200=100, atr=2, bb_upper=110, bb_lower=90, volume_spike=False,
)


class GenerateSignalTests(TestCase):
    def test_bear_dominant_is_not_buy(self):
        # bull 5 / bear 7 — used to return BUY
        ind = dict(BASE_IND, rsi=28, macd_diff=-1, ema9=95, ema21=98,
                   sma50=110, sma200=120, bb_lower=100, volume_spike=True)
        s = generate_signal(ind, 'swing')
        self.assertLess(s['bull_points'], s['bear_points'])
        self.assertNotIn('BUY', s['signal'])

    def test_strong_bearish_patterns_override_buy(self):
        # bull 5 / bear 22 — used to return BUY
        ind = dict(BASE_IND, rsi=28, macd_diff=1, ema9=99, ema21=98,
                   sma50=110, sma200=120, volume_spike=True)
        pats = [{'type': 'bearish', 'confidence': 90}] * 4
        s = generate_signal(ind, 'intraday', patterns=pats)
        self.assertIn('SELL', s['signal'])

    def test_clear_bullish_is_buy(self):
        ind = dict(BASE_IND, rsi=40, macd_diff=1, ema9=101, ema21=99, sma50=90, sma200=80)
        s = generate_signal(ind, 'swing')
        self.assertIn('BUY', s['signal'])
        self.assertIsNone(s['short_sell'])
        self.assertLess(s['stop_loss'], s['entry_price'])
        self.assertGreater(s['target1'], s['entry_price'])

    def test_sell_signal_has_short_levels_on_correct_side(self):
        ind = dict(BASE_IND, rsi=75, macd_diff=-1, ema9=98, ema21=101, sma50=110, sma200=120)
        s = generate_signal(ind, 'swing')
        self.assertIn('SELL', s['signal'])
        ss = s['short_sell']
        self.assertIsInstance(ss, dict)
        self.assertGreater(ss['stop_loss'], ss['sell_at'])
        self.assertLess(ss['target1'], ss['sell_at'])
        self.assertLess(ss['target2'], ss['target1'])

    def test_balanced_is_wait(self):
        # bull: macd +2, price>sma50 +1  |  bear: ema9<ema21 +2, price<sma200 +1
        ind = dict(BASE_IND, macd_diff=1, ema9=99, ema21=101, sma50=95, sma200=105)
        s = generate_signal(ind, 'swing')
        self.assertEqual((s['bull_points'], s['bear_points']), (3, 3))
        self.assertEqual(s['signal'], 'WAIT')


class PaperTradeValidationTests(TestCase):
    def setUp(self):
        self.user = make_user('trader')
        self.client.force_login(self.user)

    def open(self, **payload):
        body = dict(symbol='TCS', trade_type='swing', entry_price=100,
                    stop_loss=95, target1=105, target2=110, quantity=2)
        body.update(payload)
        return self.client.post('/api/paper-trade/open/', json.dumps(body),
                                content_type='application/json')

    def test_valid_buy_opens(self):
        r = self.open(direction='buy')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(PaperTrade.objects.count(), 1)

    def test_short_with_long_levels_rejected(self):
        r = self.open(direction='sell')  # SL below entry — would instantly "hit"
        self.assertEqual(r.status_code, 400)
        self.assertEqual(PaperTrade.objects.count(), 0)

    def test_valid_short_opens(self):
        r = self.open(direction='sell', stop_loss=105, target1=95, target2=90)
        self.assertEqual(r.status_code, 200)

    def test_buy_with_sl_above_entry_rejected(self):
        self.assertEqual(self.open(direction='buy', stop_loss=101).status_code, 400)

    def test_garbage_input_is_400_not_500(self):
        self.assertEqual(self.open(direction='buy', entry_price='abc').status_code, 400)
        self.assertEqual(self.open(direction='hold').status_code, 400)
        self.assertEqual(self.open(direction='buy', quantity=0).status_code, 400)

    def test_close_rejects_zero_exit_price(self):
        self.open(direction='buy')
        t = PaperTrade.objects.get()
        r = self.client.post('/api/paper-trade/close/',
                             json.dumps({'trade_id': t.id, 'exit_price': 0}),
                             content_type='application/json')
        self.assertEqual(r.status_code, 400)
        r = self.client.post('/api/paper-trade/close/',
                             json.dumps({'trade_id': t.id, 'exit_price': 104, 'result': 'bogus'}),
                             content_type='application/json')
        self.assertEqual(r.status_code, 200)
        t.refresh_from_db()
        self.assertEqual(t.result, 'manual')
        self.assertEqual(t.pnl, 8.0)


class CsrfAndTokenLeakTests(TestCase):
    def setUp(self):
        self.user = make_user('victim')
        self.client = Client(enforce_csrf_checks=True)
        self.client.force_login(self.user)

    def test_post_without_csrf_token_is_rejected(self):
        for url in ('/api/watchlist/', '/api/upstox-token/',
                    '/api/paper-trade/open/', '/api/paper-trade/close/'):
            r = self.client.post(url, '{"symbol":"TCS","token":"x"}', content_type='text/plain')
            self.assertEqual(r.status_code, 403, url)

    def test_post_with_csrf_token_works(self):
        # Dashboard must set the csrftoken cookie that main.js reads
        self.client.get('/trades/')
        token = self.client.cookies['csrftoken'].value
        r = self.client.post('/api/watchlist/', json.dumps({'symbol': 'TCS'}),
                             content_type='application/json', HTTP_X_CSRFTOKEN=token)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['status'], 'added')

    def test_ws_token_endpoint_removed(self):
        self.assertEqual(self.client.get('/api/ws-token/').status_code, 404)


class AdminGuardTests(TestCase):
    def setUp(self):
        self.root = User.objects.create_superuser('root', 'root@example.com', 'x')
        self.admin = make_user('staffadmin', is_admin=True)
        self.admin2 = make_user('otheradmin', is_admin=True)
        self.normal = make_user('normal')
        self.client.force_login(self.admin)

    def test_cannot_delete_superuser(self):
        r = self.client.post(f'/admin-panel/users/{self.root.id}/reject/')
        self.assertEqual(r.status_code, 404)
        self.assertTrue(User.objects.filter(id=self.root.id).exists())

    def test_cannot_deactivate_self_or_other_admin(self):
        self.client.post(f'/admin-panel/users/{self.admin.id}/deactivate/')
        self.client.post(f'/admin-panel/users/{self.admin2.id}/reject/')
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)
        self.assertTrue(User.objects.filter(id=self.admin2.id).exists())

    def test_can_manage_normal_user(self):
        self.client.post(f'/admin-panel/users/{self.normal.id}/deactivate/')
        self.normal.refresh_from_db()
        self.assertFalse(self.normal.is_active)

    def test_superuser_can_manage_admins(self):
        self.client.force_login(self.root)
        self.client.post(f'/admin-panel/users/{self.admin2.id}/reject/')
        self.assertFalse(User.objects.filter(id=self.admin2.id).exists())


class ForgotPasswordTests(TestCase):
    def setUp(self):
        self.user = make_user('forgetful', password='Old-pass-123', email='forget@example.com')

    def test_login_page_links_to_forgot_password(self):
        r = self.client.get('/login/')
        self.assertContains(r, 'href="/password-reset/"')
        self.assertContains(r, 'Forgot password?')

    def test_full_reset_flow(self):
        r = self.client.get('/password-reset/')
        self.assertEqual(r.status_code, 200)

        r = self.client.post('/password-reset/', {'email': 'forget@example.com'})
        self.assertRedirects(r, '/password-reset/sent/')
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('forgetful', mail.outbox[0].body)

        link = re.search(r'https?://[^/]+(/password-reset/\S+/)', mail.outbox[0].body).group(1)
        r = self.client.get(link, follow=True)  # Django swaps token for a session URL
        self.assertContains(r, 'Set a new password')
        set_url = r.redirect_chain[-1][0]

        r = self.client.post(set_url, {'new_password1': 'Brand-new-pass-456',
                                       'new_password2': 'Brand-new-pass-456'})
        self.assertRedirects(r, '/password-reset/complete/')

        self.assertTrue(self.client.login(username='forgetful', password='Brand-new-pass-456'))
        self.assertFalse(self.client.login(username='forgetful', password='Old-pass-123'))

    def test_unknown_email_sends_nothing_but_same_response(self):
        r = self.client.post('/password-reset/', {'email': 'nobody@example.com'})
        self.assertRedirects(r, '/password-reset/sent/')  # no account enumeration
        self.assertEqual(len(mail.outbox), 0)

    def test_used_link_is_invalid(self):
        self.client.post('/password-reset/', {'email': 'forget@example.com'})
        link = re.search(r'https?://[^/]+(/password-reset/\S+/)', mail.outbox[0].body).group(1)
        r = self.client.get(link, follow=True)
        self.client.post(r.redirect_chain[-1][0], {'new_password1': 'Brand-new-pass-456',
                                                   'new_password2': 'Brand-new-pass-456'})
        fresh = Client()
        r = fresh.get(link, follow=True)
        self.assertContains(r, 'Link expired')


EVIL_RSS = b"""<?xml version="1.0"?><rss><channel>
<item><title>TCS &lt;img src=x onerror=alert(1)&gt; results</title><link>javascript:alert(document.cookie)</link></item>
<item><title>TCS shares rally</title><link>https://example.com/tcs</link></item>
</channel></rss>"""


class NewsXssTests(TestCase):
    def setUp(self):
        self.client.force_login(make_user('reader'))

    def fake_get(self, *a, **kw):
        return mock.Mock(content=EVIL_RSS)

    def test_stock_news_drops_javascript_links(self):
        with mock.patch('requests.get', side_effect=self.fake_get):
            news = self.client.get('/api/stock-news/?symbol=TCS').json()['news']
        self.assertTrue(news)
        self.assertTrue(all(n['link'].startswith('https://') for n in news))

    def test_market_news_drops_javascript_links(self):
        with mock.patch('requests.get', side_effect=self.fake_get):
            news = self.client.get('/api/news/').json()['news']
        self.assertTrue(news)
        self.assertTrue(all(n['link'].startswith('https://') for n in news))

    def test_frontend_renderers_escape_rss_text(self):
        root = Path(__file__).resolve().parent.parent
        main_js = (root / 'static/js/main.js').read_text(encoding='utf-8')
        news = (root / 'templates/stocks/news.html').read_text(encoding='utf-8')
        for src in (main_js, news):
            self.assertNotRegex(src, r'href="\$\{(n|item)\.link\}"')
            self.assertNotRegex(src, r'>\$\{(n|item)\.title\}<')
        self.assertIn('${escapeHtml(n.title)}', main_js)
        self.assertIn('${escapeHtml(item.title)}', news)


class ProductionSettingsTests(TestCase):
    """Load settings in a fresh process with a Railway-like environment."""

    def load_settings(self, **env):
        root = Path(__file__).resolve().parent.parent
        clean = {k: v for k, v in os.environ.items()
                 if k not in ('DEBUG', 'SECRET_KEY', 'RAILWAY_ENVIRONMENT')}
        clean.update(env)
        code = ('import json, stockanalyzer.settings as s; '
                'print(json.dumps({"debug": s.DEBUG, "hosts": s.ALLOWED_HOSTS, '
                '"secure": getattr(s, "SESSION_COOKIE_SECURE", False)}))')
        # Override load_dotenv so a local .env cannot leak into the test
        code = 'import dotenv; dotenv.load_dotenv = lambda *a, **k: None; ' + code
        return subprocess.run([sys.executable, '-c', code], cwd=root, env=clean,
                              capture_output=True, text=True)

    def test_railway_without_secret_key_refuses_to_start(self):
        r = self.load_settings(RAILWAY_ENVIRONMENT='production')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('SECRET_KEY environment variable must be set', r.stderr)

    def test_railway_defaults_are_safe(self):
        r = self.load_settings(RAILWAY_ENVIRONMENT='production', SECRET_KEY='x' * 60)
        self.assertEqual(r.returncode, 0, r.stderr)
        cfg = json.loads(r.stdout)
        self.assertFalse(cfg['debug'])
        self.assertTrue(cfg['secure'])
        self.assertNotIn('*', cfg['hosts'])

    def test_local_dev_still_works_without_env(self):
        r = self.load_settings()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(json.loads(r.stdout)['debug'])


VALID_SIGNUP = dict(
    first_name='Rashid', last_name='Khan', email='Rashid@Gmail.com',
    country_code='+91', phone='98765 43210',
    trading_experience='beginner', preferred_trade_type='swing', capital='50000',
    username='Rashid_Trader', password='Sw1ng-Trade!x', confirm_password='Sw1ng-Trade!x',
)


class RegisterValidationTests(TestCase):
    def signup(self, **over):
        return self.client.post('/register/', dict(VALID_SIGNUP, **over))

    def test_valid_signup_saves_lowercase_and_e164(self):
        r = self.signup()
        self.assertRedirects(r, '/login/?registered=1', fetch_redirect_response=False)
        u = User.objects.get()
        self.assertEqual(u.username, 'rashid_trader')
        self.assertEqual(u.email, 'rashid@gmail.com')
        self.assertEqual(u.profile.phone, '+919876543210')

    def test_username_rules(self):
        for bad in ('1rashid', '_rashid', 'ra', 'rashid@123', 'rashid khan', 'r' * 21):
            r = self.signup(username=bad)
            self.assertEqual(r.status_code, 200, bad)
            self.assertContains(r, 'Username must start with a letter')
        self.assertEqual(User.objects.count(), 0)

    def test_duplicate_username_email_phone_rejected(self):
        self.signup()
        r = self.signup(username='RASHID_TRADER', email='other@x.com', phone='9123456780')
        self.assertContains(r, 'This username is already taken.')
        r = self.signup(username='second', email='RASHID@gmail.com', phone='9123456780')
        self.assertContains(r, 'This email is already registered.')
        r = self.signup(username='third', email='third@x.com', phone='9876543210')
        self.assertContains(r, 'This phone number is already registered.')
        self.assertEqual(User.objects.count(), 1)

    def test_legacy_phone_without_country_code_counts_as_duplicate(self):
        old = make_user('legacy')
        old.profile.phone = '9876543210'
        old.profile.save()
        r = self.signup()
        self.assertContains(r, 'This phone number is already registered.')

    def test_phone_rules_per_country(self):
        self.assertContains(self.signup(phone='12345'), 'must be 10 digits for +91')
        self.assertContains(self.signup(phone='5876543210'), 'start with 6, 7, 8 or 9')
        self.assertContains(self.signup(phone='98765abc10'), 'digits only')
        r = self.signup(country_code='+971', phone='501234567')
        self.assertEqual(User.objects.get().profile.phone, '+971501234567')

    def test_password_rules_and_errors_shown_with_values_kept(self):
        r = self.signup(password='12345678', confirm_password='12345678')
        self.assertContains(r, 'entirely numeric')
        self.assertContains(r, 'value="Rashid"')          # entered values preserved
        self.assertContains(r, 'value="Rashid@Gmail.com"')
        self.assertNotContains(r, '12345678')            # password never echoed back
        r = self.signup(confirm_password='different-1A')
        self.assertContains(r, 'Passwords do not match.')

    def test_page_shows_country_picker(self):
        r = self.client.get('/register/')
        self.assertContains(r, 'name="country_code"')
        self.assertContains(r, 'India (+91)')


class LoginAndCsrfFailureTests(TestCase):
    def test_login_is_case_insensitive(self):
        make_user('justin001', password='Str0ng-pass-123')
        r = self.client.post('/login/', {'username': 'Justin001', 'password': 'Str0ng-pass-123'})
        self.assertEqual(r.status_code, 302)

    def test_stale_login_form_redirects_with_message_not_403(self):
        c = Client(enforce_csrf_checks=True)
        c.get('/login/')
        r = c.post('/login/', {'username': 'x', 'password': 'y', 'csrfmiddlewaretoken': 'stale-token'})
        self.assertRedirects(r, '/login/?expired=1', fetch_redirect_response=False)
        self.assertContains(c.get('/login/?expired=1'), 'Your page had expired')

    def test_api_csrf_failure_is_json(self):
        c = Client(enforce_csrf_checks=True)
        c.force_login(make_user('apiuser'))
        r = c.post('/api/watchlist/', '{}', content_type='application/json')
        self.assertEqual(r.status_code, 403)
        self.assertIn('Session expired', r.json()['error'])

    def test_login_page_not_cached(self):
        r = self.client.get('/login/')
        self.assertIn('no-cache', r['Cache-Control'])


class ProfileUniquenessTests(TestCase):
    def setUp(self):
        self.a = make_user('alice', email='alice@x.com')
        self.b = make_user('bob', email='bob@x.com')
        self.b.profile.phone = '+919876543210'
        self.b.profile.save()
        self.client.force_login(self.a)

    def post(self, **kw):
        data = dict(first_name='Alice', email='alice@x.com', country_code='+91', phone='',
                    trading_experience='beginner', preferred_trade_type='swing')
        data.update(kw)
        return self.client.post('/profile/', data)

    def test_cannot_take_other_users_email_or_phone(self):
        r = self.post(email='BOB@x.com')
        self.assertIn('error=', r['Location'])
        r = self.post(phone='9876543210')
        self.assertIn('error=', r['Location'])
        self.a.refresh_from_db()
        self.assertEqual(self.a.email, 'alice@x.com')
        self.assertEqual(self.a.profile.phone, '')

    def test_valid_update_saves_e164(self):
        r = self.post(phone='9123456780')
        self.assertEqual(r['Location'], '/profile/?saved=1')
        self.a.profile.refresh_from_db()
        self.assertEqual(self.a.profile.phone, '+919123456780')


class NormalizeMigrationTests(TestCase):
    def test_normalize_existing_data(self):
        import importlib
        from django.apps import apps as django_apps
        mig = importlib.import_module('stocks.migrations.0006_normalize_users_unique_phone')
        u = make_user('Justin001', email='Justin001James@Gmail.com')
        u.profile.phone = '7034956971'
        u.profile.save()
        mig.normalize(django_apps, None)
        u.refresh_from_db()
        self.assertEqual(u.username, 'justin001')
        self.assertEqual(u.email, 'justin001james@gmail.com')
        self.assertEqual(u.profile.phone, '+917034956971')
