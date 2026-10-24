import sqlite3
import unittest

from group_reports import pdf_sections
from studies import get_templates, initialize_studies
from study_pdf import render_study_pdf


class GroupReportSchemaTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        initialize_studies(self.db)

    def tearDown(self):
        self.db.close()

    def test_template_fields_receive_stable_keys_and_keep_them(self):
        first = get_templates(self.db)[0]['fields']
        keys = [field['stable_key'] for field in first]
        self.assertTrue(all(keys))
        self.assertEqual(len(keys), len(set(keys)))
        initialize_studies(self.db)
        self.assertEqual(keys, [field['stable_key'] for field in get_templates(self.db)[0]['fields']])

    def test_group_drafts_and_history_numbers_are_unique(self):
        columns = {row[1] for row in self.db.execute('PRAGMA table_info(study_template)')}
        self.assertIn('owner_school', columns)
        self.db.execute("INSERT INTO group_report_draft VALUES('g1','s1','学校',1,'{}','{}','now',0)")
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("INSERT INTO group_report_draft VALUES('g1','s1','学校',1,'{}','{}','now',0)")
        self.db.execute("INSERT INTO group_report_version(group_id,school_id,school_name,version_number,template_snapshot,report_snapshot,saved_by,saved_at) VALUES('g1','s1','学校',1,'{}','{}','u','now')")
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("INSERT INTO group_report_version(group_id,school_id,school_name,version_number,template_snapshot,report_snapshot,saved_by,saved_at) VALUES('g1','s1','学校',1,'{}','{}','u','later')")

    def test_pdf_sections_follow_template_order_and_render_metadata(self):
        template = get_templates(self.db)[0]
        template['fields'][1]['field_type'] = 'image'
        values = {field['stable_key']: {'value': field['label'], 'images': []} for field in template['fields']}
        sections = pdf_sections(self.db, '.', template, values, '学校：テスト\nバージョン：1')
        self.assertEqual(sections[0][0], '保存情報')
        self.assertEqual([item['label'] if isinstance(item, dict) else item[0] for item in sections[1:]],
                         [field['label'] for field in template['fields']])
        self.assertTrue(render_study_pdf(sections).startswith(b'%PDF'))


if __name__ == '__main__':
    unittest.main()
