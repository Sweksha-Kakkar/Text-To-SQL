"""Download two public datasets and load them into TableTalk, the same way an upload does.

Run from the project folder:  python -m scripts.load_real_data
  IPL ball-by-ball data   https://cricsheet.org
  Online Retail (UK shop) https://archive.ics.uci.edu/dataset/352  (CC BY 4.0)
Files are kept in data/ (not committed).
"""
import io
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core import datasets  # noqa: E402

SOURCES = [
    {
        "title": "IPL", "table": "deliveries", "member": "all_matches.csv", "file": "ipl.zip",
        "url": "https://cricsheet.org/downloads/ipl_male_csv2.zip",
        "rules": "\n".join([
            "Each row of deliveries is one ball bowled. A match is identified by match_id.",
            "A batter's runs = SUM(runs_off_bat) grouped by striker. Extras are not the batter's runs.",
            "A team's total runs = SUM(runs_off_bat + extras) grouped by batting_team.",
            "A four is runs_off_bat = 4 and a six is runs_off_bat = 6.",
            "A bowler's wickets count only wicket_type in ('bowled', 'caught', 'caught and bowled', "
            "'lbw', 'stumped', 'hit wicket'). Run outs are not the bowler's wicket.",
            "Innings 3 and above are super overs: exclude them (innings <= 2) from batting and bowling totals.",
        ]),
    },
    {
        "title": "Online retail", "table": "sales", "member": "Online Retail.xlsx", "file": "retail.zip",
        "url": "https://archive.ics.uci.edu/static/public/352/online+retail.zip",
        "rules": "\n".join([
            "Each row of sales is one product line on an invoice.",
            "Invoice numbers starting with 'C' are cancellations.",
            "Revenue = quantity * unit_price, counting only rows with quantity > 0, unit_price > 0 "
            "and an invoice_no not starting with 'C'.",
            "An order is a distinct invoice_no that is not a cancellation.",
            "Rows with no customer_id are guests: leave them out of questions about customers.",
        ]),
    },
]


def main() -> None:
    folder = ROOT / "data"
    folder.mkdir(exist_ok=True)
    existing = {d.title for d in datasets.list_datasets()}
    for src in SOURCES:
        if src["title"] in existing:
            print(f"{src['title']}: already loaded")
            continue
        path = folder / src["file"]
        if not path.exists():
            print(f"{src['title']}: downloading…")
            urllib.request.urlretrieve(src["url"], path)
        with zipfile.ZipFile(path) as z:
            data = z.read(src["member"])  # read into memory, nothing is extracted to disk
        suffix = Path(src["member"]).suffix
        print(f"{src['title']}: reading and loading…")
        frames = datasets.read_files([(src["table"] + suffix, io.BytesIO(data).getvalue())])
        ds, notes = datasets.create_dataset(src["title"], frames, src["rules"])
        print(f"{ds.title}: loaded {sum(len(f) for f in frames.values()):,} rows")
        for note in notes:
            print("  note:", note)


if __name__ == "__main__":
    main()
