from google.cloud import datastore


def get_sites():
    client = datastore.Client()
    query = client.query(kind='Site')
    query.add_filter('acute', '=', 'no')
    sites = list(query.fetch())

    return sites


def get_site(name, code):
    client = datastore.Client()
    print(f"Getting site: {name}/{code}")
    key = client.key('Site', name)
    site = client.get(key)
    if site and site.get('code') == code:
        return site

    print(f"No site detected in db: {name}/{code}")
    return None


def get_ppe_items_from_db():
    client = datastore.Client()
    query=client.query(kind='Ppe-Item')
    query.add_filter('quantity_used', '>', 0)
    items = list(query.fetch())
    return items
