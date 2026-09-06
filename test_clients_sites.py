import os
import sqlite3
import tempfile
import unittest

import app


class ClientSiteRelationshipTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db_path = app.DB_PATH
        app.DB_PATH = os.path.join(self.temp_dir.name, 'test.db')
        app.init_db()

    def tearDown(self):
        app.DB_PATH = self.original_db_path
        self.temp_dir.cleanup()

    def test_multiple_sites_are_grouped_under_client_and_company_scoped(self):
        conn = app.db()
        companies = conn.execute('SELECT id FROM companies ORDER BY id').fetchall()
        company_id, other_company_id = companies[0]['id'], companies[1]['id']
        conn.execute(
            "INSERT INTO clients (company_id, name, active, created_at) VALUES (?, 'Test Customer', 1, ?)",
            (company_id, app.utc_now_str()),
        )
        client_id = conn.execute(
            "SELECT id FROM clients WHERE company_id=? AND name='Test Customer'", (company_id,)
        ).fetchone()['id']
        other_client_id = conn.execute(
            'SELECT id FROM clients WHERE company_id=? ORDER BY id LIMIT 1', (other_company_id,)
        ).fetchone()['id']
        conn.execute(
            "INSERT INTO sites (company_id, client_id, name, client_company_name, address_line1, city, state, zip_code) VALUES (?, ?, 'Warehouse', 'Customer', '1 Main', 'Austin', 'TX', '78701')",
            (company_id, client_id),
        )
        conn.execute(
            "INSERT INTO sites (company_id, client_id, name, client_company_name) VALUES (?, ?, 'Truck Yard', 'Customer')",
            (company_id, client_id),
        )
        conn.execute(
            "INSERT INTO sites (company_id, client_id, name, client_company_name) VALUES (?, ?, 'Hidden Site', 'Other')",
            (other_company_id, other_client_id),
        )
        conn.commit()
        conn.close()

        context = app.client_site_management_context(company_id)
        client = next(row for row in context['client_records'] if row['id'] == client_id)
        self.assertEqual({'Warehouse', 'Truck Yard'}, {site['name'] for site in client['sites']})
        self.assertNotIn('Hidden Site', {site['name'] for site in context['site_records']})
        self.assertEqual('1 Main, Austin, TX 78701', next(site for site in client['sites'] if site['name'] == 'Warehouse')['physical_address'])

    def test_non_destructive_migration_keeps_legacy_address_and_relationship(self):
        conn = app.db()
        site = conn.execute('SELECT id, client_id, address FROM sites WHERE client_id IS NOT NULL LIMIT 1').fetchone()
        original = dict(site)
        conn.close()

        app.init_db()

        conn = app.db()
        migrated = conn.execute('SELECT * FROM sites WHERE id=?', (original['id'],)).fetchone()
        client_columns = app.column_names(conn, 'clients')
        site_columns = app.column_names(conn, 'sites')
        conn.close()
        self.assertEqual(original['client_id'], migrated['client_id'])
        self.assertEqual(original['address'], migrated['address'])
        self.assertTrue({'address_line1', 'address_line2', 'city', 'state', 'zip_code'} <= client_columns)
        self.assertTrue({'address_line1', 'address_line2', 'city', 'state', 'zip_code'} <= site_columns)

    def test_schema_declares_site_client_foreign_key(self):
        conn = sqlite3.connect(app.DB_PATH)
        foreign_keys = conn.execute('PRAGMA foreign_key_list(sites)').fetchall()
        conn.close()
        self.assertTrue(any(row[2] == 'clients' and row[3] == 'client_id' and row[4] == 'id' for row in foreign_keys))


if __name__ == '__main__':
    unittest.main()
