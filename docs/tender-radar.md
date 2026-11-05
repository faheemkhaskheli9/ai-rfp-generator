# Pakistan IT Tender Radar — zero-budget pilot

This is a separate public-procurement research module in the existing RFP generator repository; it does not change the application's core code. It scans a small sample of the federal PPRA active tender listing for IT/software notices, producing Markdown and JSON output under tender_radar/data/.

## Run and test

Install beautifulsoup4, then run:

    python -m unittest discover -s tender_radar/tests -p test_*.py -v
    python tender_radar/radar.py --pages 3 --output-dir tender_radar/data

The workflow runs approximately 09:00 PKT on weekdays once merged into the default branch, or can be manually dispatched. It does not require an LLM, an API key, or paid infrastructure.

The collector **fails closed** when the source format changes. Notices with expired/unknown closing dates, cancelled statuses, or no IT-related keywords are omitted. The first release scans only three pages (configurable up to five). This is NOT a comprehensive tender coverage product, and keyword matching is NOT a bid-eligibility assessment.

## 5 November 2026: manually researched sample

These were observed in the official federal PPRA listing. They are buyers' tender opportunities, NOT paying customers or companies agreeing to subscribe to our service.

| ID | Buyer | Topic | Listed closing (PKT) |
| --- | --- | --- | --- |
| TS0000017259E | National Information Technology Board | Integration of Online Payment Services | 17 Nov 2026, 11:00 |
| TS0000017519E | Pakistan Digital Authority | Citizen Experience Design Lab | 16 Nov 2026, 15:00 |
| TS0000017526E | Pakistan Digital Authority | Supply & Delivery of Laptops | 16 Nov 2026, 12:00 |
| TS0000016755E | Energy Infrastructure Development and Management Company | Procurement of IT Equipment | 10 Nov 2026, 11:00 |

Source: https://epms.ppra.gov.pk/public/tenders/active-tenders

## Validation before commercial launch

1. Verify permission to access the public source programmatically, including robots/terms and reasonable request rate. If disallowed, switch to an authorized feed.
2. Validate live HTML parsing with at least 10 official records, latest amendments and actual deadlines.
3. Compare daily snapshots against the official list for missing records and false positives; expand coverage to other sources only after validation.
4. Conduct five manually reviewed, permission-conscious conversations with potential IT suppliers, offering one free sample digest.
5. Test PKR 2,000–5,000/month pilot prices, then build customer-specific matching and delivery only after a paid commitment.

No unsolicited messages, bids, payments, or customer interactions are automated. Revenue: PKR 0. Customers: 0. Outreach: 0. The workflow generates data, not sales.
