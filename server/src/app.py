import json
import datetime
import time
from pprint import pprint

from flask import Flask, render_template, make_response, redirect, url_for, g
import os

from flask_oidc import OpenIDConnect
from google.cloud import datastore
from google.cloud import pubsub_v1
from okta import UsersClient

app = Flask(__name__)

client_secrets = {'web': {
    'client_id': os.getenv('OKTA_CLIENT_ID'),
    'client_secret': os.getenv('OKTA_CLIENT_SECRET'),
    'auth_uri': f'{os.getenv("OKTA_ORG_URL")}/oauth2/default/v1/authorize',
    'token_uri': f'{os.getenv("OKTA_ORG_URL")}/oauth2/default/v1/token',
    'issuer': f'{os.getenv("OKTA_ORG_URL")}/oauth2/default',
    'userinfo_uri': f'{os.getenv("OKTA_ORG_URL")}/oauth2/default/v1/userinfo',
    'redirect_uris': os.getenv('OIDC_REDIRECT_URIS')
}}

with open('client_secrets.json', 'w') as fp:
    json.dump(client_secrets, fp)

app.config['OIDC_CLIENT_SECRETS'] = 'client_secrets.json'
app.config['OIDC_COOKIE_SECURE'] = os.getenv('OIDC_COOKIE_SECURE')
app.config['OIDC_CALLBACK_ROUTE'] = os.getenv('OIDC_CALLBACK_ROUTE')
app.config['OIDC_SCOPES'] = ["openid", "email", "profile"]
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY')
oidc = OpenIDConnect(app)

pprint(app.config)
pprint(oidc.client_secrets)

okta_client = UsersClient(os.getenv('OKTA_ORG_URL'), os.getenv('OKTA_AUTH_TOKEN'))

currentTime = datetime.datetime.now()

client = datastore.Client()


@app.route('/')
def index():
    return render_template('index.html',
                           sites=[],
                           search_type='search_type',
                           args='args',
                           result_label='Welcome to the PPE Inventory Management App')


@app.route('/sites')
@oidc.require_login
def sites():
    return render_template('sites.html',
                           sites=[],
                           search_type='search_type',
                           args='args',
                           result_label='Sites page')


@app.route('/sites/<site_param>')
@oidc.require_login
def site(site_param):
    if site_param == 'DUMMY':
        site_entity = 'DUMMY entity'
    else:
        key = client.key('Site', site_param)
        site_entity = client.get(key)
    return render_template('form.html', site_entity=site_entity)


@app.route('/dashboard')
@oidc.require_login
def dashboard():
    return render_template('dashboard.html',
                           sites=[],
                           search_type='search_type',
                           args='args',
                           result_label='Dashboard page')


@app.route('/login')
@oidc.require_login
def login():
    return redirect(url_for('.index'))


@app.route('/logout')
def logout():
    oidc.logout()
    return redirect(url_for('.index'))


@app.before_request
def inject_user_into_each_request():
    if oidc.user_loggedin:
        g.user = okta_client.get_user(str(oidc.user_getfield('sub')))
    else:
        g.user = None


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))


def form(request):
    name = request.cookies.get('site')
    code = request.cookies.get('code')

    client = datastore.Client()

    site = None
    post = False
    if name and code:
        site = get_site(name, code, client)

    if site and request.method == 'POST':
        update_site(site, client, request, code)
        publish_update(get_sheet_data(site))
        post = True

    # Construct a full URL to redirect to
    # otherwise we seem to end up on http
    domain = os.getenv('DOMAIN')
    form_action = f'https://{domain}/form'

    if post:
        template = 'success.html'
    elif site and 'acute' in site.keys() and site['acute'] == 'yes':
        template = 'form.html'
    elif site:
        template = 'community_form.html'
    else:
        template = 'error.html'

    # template = 'success.html' if post else 'form.html' if site else 'error.html'
    print(f"Rendering {template}")

    response = make_response(render_template(template,
                                             site=site,
                                             form_action=form_action,
                                             currentTime=datetime.datetime.now().strftime('%H:%M %d %B %y'),
                                             assets='https://storage.googleapis.com/ppe-inventory',
                                             data={}
                                             ))

    if site:
        # Refresh the cookie
        expire_date = datetime.datetime.now() + datetime.timedelta(days=90)
        response.set_cookie('site', site.key.name, expires=expire_date, secure=True)
        response.set_cookie('code', site['code'], expires=expire_date, secure=True)

    return response


def get_site(name, code, client):
    print(f"Getting site: {name}/{code}")
    key = client.key('Site', name)
    site = client.get(key)
    if site and site.get('code') == code:
        return site
    return None


def update_site(site, client, request, code):
    acute = site.get('acute')
    print(f"Updating site: {site}/{code}")
    # Update the site
    site.update(request.form)

    site["last_update"] = datetime.datetime.now()

    # Values not to change
    site['site'] = site.key.name
    site['acute'] = acute
    site['code'] = code

    print(f"Updating site {site}")
    client.put(site)


def publish_update(site):
    # Publish a message to update the Google Sheet:

    message = {}
    message.update(site)
    message['last_update'] = (datetime.datetime.now() + datetime.timedelta(hours=1)).strftime('%H:%M %d %B %y')

    publisher = pubsub_v1.PublisherClient()

    project_id = os.getenv("PROJECT_ID")
    topic_path = publisher.topic_path(project_id, 'form-submissions')

    data = json.dumps(message).encode("utf-8")

    future = publisher.publish(topic_path, data=data)

    print(f"Published update to site {site.key.name}: {future.result()}")


def get_sheet_data(site):
    safe_site_data = datastore.Entity(key=datastore.Client().key('Site', site['site']))
    fields = [
        'site',
        'face-visors-stock-levels',
        'face-visors-quantity_used',
        'face-visors-stock-levels-note',
        'face-visors-rag',
        'goggles-stock-levels',
        'goggles-quantity_used',
        'goggles-stock-levels-note',
        'goggles-rag',
        'masks-iir-stock-levels',
        'masks-iir-quantity_used',
        'masks-iir-stock-levels-note',
        'masks-iir-rag',
        'masks-ffp2-stock-levels',
        'masks-ffp2-quantity_used',
        'masks-ffp2-stock-levels-note',
        'masks-ffp2-rag',
        'masks-ffp3-stock-levels',
        'masks-ffp3-quantity_used',
        'masks-ffp3-stock-levels-note',
        'masks-ffp3-rag',
        'fit-test-solution-stock-levels',
        'fit-test-solution-quantity_used',
        'fit-test-solution-stock-levels-note',
        'fit-test-solution-rag',
        'fit-test-fullkit-stock-levels',
        'fit-test-fullkit-quantity_used',
        'fit-test-fullkit-stock-levels-note',
        'fit-test-fullkit-rag',
        'gloves-stock-levels',
        'gloves-quantity_used',
        'gloves-stock-levels-note',
        'gloves-rag',
        'gowns-stock-levels',
        'gowns-quantity_used',
        'gowns-stock-levels-note',
        'gowns-rag',
        'hand-hygiene-stock-levels',
        'hand-hygiene-quantity_used',
        'hand-hygiene-stock-levels-note',
        'hand-hygiene-rag',
        'apron-stock-levels',
        'apron-quantity_used',
        'apron-stock-levels-note',
        'apron-rag',
        'body-bags-stock-levels',
        'body-bags-quantity_used',
        'body-bags-stock-levels-note',
        'body-bags-rag',
        'coveralls-stock-levels',
        'coveralls-quantity_used',
        'coveralls-stock-levels-note',
        'coveralls-rag',
        'swabs-stock-levels',
        'swabs-quantity_used',
        'swabs-stock-levels-note',
        'swabs-rag',
        'fit-test-solution-55ml-stock-levels',
        'fit-test-solution-55ml-quantity_used',
        'fit-test-solution-55ml-stock-levels-note',
        'fit-test-solution-55ml-rag',
        'non-covid19-patient-number',
        'covid19-patient-number',
        'covid19-patient-number-suspected',
        'staff-number',
        'gowns-mutual_aid_received',
        'gowns-national_and_other_external_receipts',
        'coveralls-mutual_aid_received',
        'coveralls-national_and_other_external_receipts'
    ]

    for field in fields:
        try:
            safe_site_data[field] = site[field]
            print(f'field = {field} has value {safe_site_data[field]}')
        except Exception as e:
            print(e)
            print(f'problem with field = {field}')
    print(f'safe_site_data is {safe_site_data}')
    return safe_site_data
