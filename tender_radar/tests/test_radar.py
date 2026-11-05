import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from tender_radar.radar import parse_list, shortlist, parse_deadline, render

FIXTURE = """<html><table><tbody>
<tr><td>1</td><td>TS0000017259E Procurement</td><td>Integration of Online Payment Services</td>
<td>National Information Technology Board (NITB)</td><td>Published Corrigendum</td>
<td>Nov 05, 2026</td><td>Nov 17, 2026 11:00 AM</td><td>View</td></tr>
<tr><td>2</td><td>TS0000017530E Procurement</td><td>Construction of boundary wall</td>
<td>Housing Authority</td><td>Published</td><td>Nov 05, 2026</td><td>Nov 30, 2026 10:30 AM</td></tr>
<tr><td>3</td><td>TS0000017650E Procurement</td><td>Software development</td>
<td>NITB</td><td>Published</td><td>Nov 05, 2026</td><td>Nov 05, 2026 09:00 AM</td></tr>
<tr><td>4</td><td>TS0000017651E Procurement</td><td>Software development</td>
<td>NITB</td><td>Cancelled</td><td>Nov 05, 2026</td><td>Nov 30, 2026 09:00 AM</td></tr>
</tbody></table></html>"""
NOW = datetime(2026, 11, 5, 16, 0, tzinfo=ZoneInfo("Asia/Karachi"))

class RadarTests(unittest.TestCase):
    def test_parse(self):
        records = parse_list(FIXTURE)
        self.assertEqual(len(records), 4)
        self.assertEqual(records[0]["tender_id"], "TS0000017259E")
        self.assertIn("Nov 17, 2026", records[0]["closing"])

    def test_filter_dedupe_rank(self):
        records, scanned = shortlist([FIXTURE, FIXTURE], NOW)
        self.assertEqual(scanned, 4)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["tender_id"], "TS0000017259E")
        self.assertGreaterEqual(records[0]["score"], 9)

    def test_unparsable_dates_not_assumed_open(self):
        self.assertIsNone(parse_deadline(None))
        self.assertIsNone(parse_deadline("not supplied"))
        self.assertEqual(parse_deadline("Nov 17, 2026 11:00 AM").hour, 11)

    def test_report(self):
        records, scanned = shortlist([FIXTURE], NOW)
        with tempfile.TemporaryDirectory() as tmp:
            render(records, scanned, NOW, Path(tmp))
            data = json.loads((Path(tmp) / "latest.json").read_text())
            self.assertEqual(data["matched"], 1)
            self.assertIn("Payment Services", (Path(tmp) / "latest.md").read_text())

    def test_fail_closed_on_unexpected_source_shape(self):
        records, scanned = shortlist(["<html><p>No table</p></html>"], NOW)
        self.assertEqual(scanned, 0)
        self.assertEqual(records, [])

if __name__ == "__main__":
    unittest.main()
