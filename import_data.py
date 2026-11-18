#!/usr/bin/env python3
"""Google Sheet -> Neon தரவு இறக்கம் (command line).

குறிப்பு: இனி இதை இயக்க வேண்டியதில்லை — Admin உள்நுழைந்து  /admin/import  பக்கத்தில்
CSV கோப்புகளை நேரடியாக upload செய்யலாம். இந்த script விருப்பத்துக்கு மட்டும் (இரண்டும் ஒரே logic).

  python import_data.py schema
  python import_data.py libraries   Sheet1.csv
  python import_data.py newspapers  "NEWSPAPER LIST.csv"
  python import_data.py map         "LIBRARY NEWSPAPER MAP.csv"
  python import_data.py holidays    HOLIDAYS.csv
  python import_data.py leave       "staff leave days.csv"
  python import_data.py rent        RENT.csv
  python import_data.py dailywage   "DAILYWAGES PERMISSION.csv"
  python import_data.py claims      contingent.csv

முதலில் libraries, பிறகு மற்றவை.  DATABASE_URL environment variable தேவை.
"""
import sys

import importer


def show(res):
    skipped = res["skipped"]
    print(f"✅ {res['done']} பதிவுகள் ஏற்றப்பட்டன"
          + (f" | ⚠️ தவிர்க்கப்பட்டவை: {len(skipped)}" if skipped else ""))
    for s in skipped[:25]:
        print("   -", s)
    if len(skipped) > 25:
        print(f"   ... மேலும் {len(skipped) - 25}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else None
    if cmd == "schema":
        importer.do_schema()
        print("✅ schema உருவாக்கப்பட்டது")
    elif cmd in importer.IMPORTERS and len(sys.argv) >= 3:
        with open(sys.argv[2], "rb") as f:
            show(importer.IMPORTERS[cmd][0](f.read()))
    else:
        print(__doc__)
        sys.exit(1)
