"""clickup_doc_write content from files: paths, Cowork sandbox mapping, joining parts, Business Rules widths."""
import json, os, sys, tempfile, unittest
from unittest import mock
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reclickup_mcp"))
import sources
from delta2doc import fit_doc

MAIN = "# M-312-1: Tạo đơn\n\n## Acceptance Criteria\n\n| No. | Acceptance Criteria |\n| --- | --- |\n| 01 | Scenario 1: ờ ự |\n"
BR_MD = "## Business Rules\n\n| **Rule ID** | Rule Name | Specification |\n|---|---|---|\n| BR-01 | Hạn mức | Không vượt 10 \\| 20 |\n"
BR_ROWS = [{"Rule ID": "BR-01", "Rule Name": "Hạn mức", "Specification": "Dòng 1\nDòng 2 | x"},
           {"Rule ID": "BR-02", "Rule Name": "Duyệt", "Specification": "Cần quản lý duyệt"}]


def tables(delta): return [o["insert"]["table-embed"] for o in delta["ops"] if isinstance(o["insert"], dict)]
def text(delta): return "".join(o["insert"] for o in delta["ops"] if isinstance(o["insert"], str))
def widths(te): return [int(c["attributes"]["width"]) for c in te["columns"]]


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        def w(name, content):
            p = os.path.join(self.dir, name)
            with open(p, "w", encoding="utf-8") as f: f.write(content if isinstance(content, str) else json.dumps(content, ensure_ascii=False))
            return p
        self.w = w

    def test_markdown_path_matches_inline_markdown(self):
        p = self.w("M-312-1.main.md", MAIN)
        a, ta = sources.build({"markdown_path": p}); b, tb = sources.build({"markdown": MAIN})
        self.assertEqual(a, b); self.assertEqual(ta, "M-312-1: Tạo đơn"); self.assertNotIn("M-312-1: Tạo đơn", text(a))

    def test_story_and_business_rules_rows_in_one_write(self):
        main, brs = self.w("s.main.md", MAIN), self.w("s.brs.json", BR_ROWS)
        delta, title = sources.build({"markdown_path": main, "delta_path": brs})
        ts = tables(delta); self.assertEqual(len(ts), 2)
        br = ts[1]; self.assertEqual(widths(br), [140, 170, 530]); self.assertEqual(widths(fit_doc(br)), [120, 146, 454])
        self.assertIn("Dòng 1\nDòng 2 | x", "".join(o["insert"] for o in br["cells"]["2:3"]["content"]))
        ids = [r["insert"]["id"] for t in ts for r in t["rows"]]; self.assertEqual(len(ids), len(set(ids)))

    def test_business_rules_markdown_gets_preset_widths(self):
        te = tables(sources.build({"markdown": BR_MD})[0])[0]
        self.assertEqual(widths(te), [140, 170, 530]); self.assertEqual(widths(fit_doc(te)), [120, 146, 454])

    def test_second_file_keeps_its_h1(self):
        delta, title = sources.build({"files": [self.w("a.md", MAIN), self.w("b.md", "# Business Rules\n\ntext")]})
        self.assertEqual(title, "M-312-1: Tạo đơn"); self.assertIn("Business Rules", text(delta))

    def test_delta_file_and_wrapped_rows(self):
        d = sources.build({"markdown": MAIN})[0]
        a = sources.build({"files": [self.w("d.json", d)]})[0]; self.assertEqual(len(tables(a)), 1)
        b = sources.build({"delta_path": self.w("r.json", {"rules": BR_ROWS})})[0]; self.assertEqual(len(tables(b)), 1)

    def test_errors(self):
        with self.assertRaisesRegex(RuntimeError, "FILE_NOT_FOUND"): sources.build({"markdown_path": "/nope/x.md"})
        with self.assertRaisesRegex(RuntimeError, "Cowork sandbox"): sources.build({"markdown_path": "/sessions/a-b-c/mnt/X/y.md"})
        with self.assertRaisesRegex(RuntimeError, "not valid JSON"): sources.build({"files": [self.w("bad.json", "{x")]})
        with self.assertRaisesRegex(RuntimeError, "Pass the content"): sources.build({})

    def test_cowork_sandbox_paths_are_mapped(self):
        root = os.path.join(self.dir, "lams"); sess = os.path.join(root, "acct", "org")
        os.makedirs(os.path.join(sess, "abcd1234", "outputs"))
        shared = os.path.join(self.dir, "OneDrive - Co", "BA"); os.makedirs(shared)
        with open(os.path.join(shared, "s.md"), "w", encoding="utf-8") as f: f.write(MAIN)
        with open(os.path.join(sess, "abcd1234", "outputs", "o.md"), "w", encoding="utf-8") as f: f.write(MAIN)
        with open(os.path.join(sess, "local_abcd1234-0000.json"), "w") as f:
            json.dump({"processName": "quiet-calm-knuth", "folderMountNames": {shared: "BA"}, "userSelectedFolders": [shared]}, f)
        with mock.patch.dict(os.environ, {"RECLICKUP_SESSIONS_ROOT": root}):
            self.assertEqual(sources.resolve("/sessions/quiet-calm-knuth/mnt/BA/s.md"), os.path.join(shared, "s.md"))
            self.assertEqual(sources.resolve("/sessions/quiet-calm-knuth/mnt/outputs/o.md"),
                             os.path.join(sess, "abcd1234", "outputs", "o.md"))
            with self.assertRaises(RuntimeError): sources.resolve("/sessions/other-name/mnt/BA/s.md")


if __name__ == "__main__":
    unittest.main()
