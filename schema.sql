-- ============================================================
--  Contingent / வரவினங்கள் மேலாண்மை — கட்டம் 1 schema (Neon PostgreSQL)
--  மீண்டும் மீண்டும் இயக்கலாம் (IF NOT EXISTS)
-- ============================================================

-- நூலகப் பட்டியல் (பழைய Sheet1: A பெயர், B குறியீடு, C மின்னஞ்சல், D வகை)
CREATE TABLE IF NOT EXISTS libraries (
  id        SERIAL PRIMARY KEY,
  name      TEXT NOT NULL UNIQUE,
  code      TEXT UNIQUE,
  emails    TEXT NOT NULL DEFAULT '',
  lib_type  TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_libraries_type ON libraries(lib_type);

-- நாளிதழ் master (NEWSPAPER LIST): எப்போதும் 18 slot
CREATE TABLE IF NOT EXISTS newspapers (
  slot      INT PRIMARY KEY CHECK (slot BETWEEN 1 AND 18),
  name      TEXT NOT NULL DEFAULT '',
  max_rate  NUMERIC(10,2) NOT NULL DEFAULT 0
);
INSERT INTO newspapers(slot) SELECT g FROM generate_series(1,18) g
  ON CONFLICT (slot) DO NOTHING;

-- நாள்வாரி விலை: திங்கள்–சனி விலை, ஞாயிறு விலை (max_rate பழைய மாத அதிகபட்சம்; இரண்டும் 0 எனில் மட்டும் பயன்படும்)
ALTER TABLE newspapers ADD COLUMN IF NOT EXISTS weekday_rate NUMERIC(10,2) NOT NULL DEFAULT 0;
ALTER TABLE newspapers ADD COLUMN IF NOT EXISTS sunday_rate  NUMERIC(10,2) NOT NULL DEFAULT 0;

-- சிறப்பு விலை நாட்கள் (தீபாவளி, பொங்கல் மலர் போன்றவை): ஒரு நாளிதழுக்கு பல தேதிகள்
CREATE TABLE IF NOT EXISTS newspaper_special_prices (
  slot         INT  NOT NULL CHECK (slot BETWEEN 1 AND 18),
  special_date DATE NOT NULL,
  price        NUMERIC(10,2) NOT NULL DEFAULT 0,
  PRIMARY KEY (slot, special_date)
);

-- நூலகம் வாரியாக எந்த நாளிதழ் Active (LIBRARY NEWSPAPER MAP)
CREATE TABLE IF NOT EXISTS library_newspaper_map (
  library_id INT NOT NULL REFERENCES libraries(id) ON DELETE CASCADE,
  slot       INT NOT NULL CHECK (slot BETWEEN 1 AND 18),
  active     BOOLEAN NOT NULL DEFAULT FALSE,
  PRIMARY KEY (library_id, slot)
);

-- பணி நாட்கள் கணக்குக்கு
CREATE TABLE IF NOT EXISTS holidays (
  holiday_date DATE PRIMARY KEY
);
ALTER TABLE holidays ADD COLUMN IF NOT EXISTS name TEXT NOT NULL DEFAULT '';
CREATE TABLE IF NOT EXISTS staff_leave_days (
  library_id INT PRIMARY KEY REFERENCES libraries(id) ON DELETE CASCADE,
  leave_days NUMERIC(5,1) NOT NULL DEFAULT 0
);

-- வாடகை (RENT) மற்றும் தினக்கூலி அனுமதி (DAILYWAGES PERMISSION)
CREATE TABLE IF NOT EXISTS rent (
  library_id INT PRIMARY KEY REFERENCES libraries(id) ON DELETE CASCADE,
  owner_name TEXT NOT NULL DEFAULT '',
  amount     NUMERIC(12,2) NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS daily_wage_permission (
  library_id INT PRIMARY KEY REFERENCES libraries(id) ON DELETE CASCADE
);
-- அனுமதிக்கப்பட்ட தினக்கூலி பணியாளர்களின் எண்ணிக்கை (Admin முடிவு செய்வார்; இயல்பு 1).
-- நெடுவரிசை முதன்முறை சேர்க்கப்படும்போது மட்டும்: முன்பு குறியீட்டில் இருந்த 2 பேர் விதி (சின்னாளபட்டி) அப்படியே தொடரும்.
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                 WHERE table_name='daily_wage_permission' AND column_name='workers') THEN
    ALTER TABLE daily_wage_permission ADD COLUMN workers INT NOT NULL DEFAULT 1 CHECK (workers BETWEEN 1 AND 20);
    UPDATE daily_wage_permission SET workers = 2
      WHERE library_id IN (SELECT id FROM libraries WHERE name = 'கி13.சின்னாளபட்டி');
  END IF;
END $$;

-- தினக்கூலி விகிதங்கள் (Admin உள்ளிடுவது). kind: staff = நூலகப் பணியாளர், partner = கூட்டுநர், part_time = பகுதி நேர
CREATE TABLE IF NOT EXISTS wage_rates (
  kind TEXT PRIMARY KEY,
  rate NUMERIC(10,2) NOT NULL DEFAULT 0
);
INSERT INTO wage_rates(kind, rate) VALUES ('partner', 75), ('staff', 300), ('part_time', 100)
  ON CONFLICT (kind) DO NOTHING;   -- இதுவரை code-ல் இருந்த தொகைகள்; Admin மாற்றலாம்

-- மாதாந்திர contingent பதிவு. unique_key = நூலக குறியீடு + மாதம்/ஆண்டு (எ.கா. TNDPL00765012026)
CREATE TABLE IF NOT EXISTS claims (
  id            SERIAL PRIMARY KEY,
  unique_key    TEXT NOT NULL UNIQUE,
  library_name  TEXT NOT NULL,
  library_code  TEXT NOT NULL,
  month_year    TEXT NOT NULL,                -- 'MM/YYYY'
  login_email   TEXT NOT NULL DEFAULT '',
  library_type  TEXT NOT NULL DEFAULT '',

  partner_name_tamil    TEXT NOT NULL DEFAULT '',
  staff_name_tamil      TEXT NOT NULL DEFAULT '',
  part_time_staff_name  TEXT NOT NULL DEFAULT '',
  rent_owner_name       TEXT NOT NULL DEFAULT '',
  bus_purpose           TEXT NOT NULL DEFAULT '',
  bus_date              TEXT NOT NULL DEFAULT '',
  travel_purpose        TEXT NOT NULL DEFAULT '',
  travel_date           TEXT NOT NULL DEFAULT '',
  register_item         TEXT NOT NULL DEFAULT '',
  stationery_item       TEXT NOT NULL DEFAULT '',
  electrical_item       TEXT NOT NULL DEFAULT '',
  consumer_item         TEXT NOT NULL DEFAULT '',
  other_expense1_desc   TEXT NOT NULL DEFAULT '',
  other_expense2_desc   TEXT NOT NULL DEFAULT '',

  partner_days            NUMERIC(12,2) NOT NULL DEFAULT 0,
  staff_days              NUMERIC(12,2) NOT NULL DEFAULT 0,
  part_time_staff_days    NUMERIC(12,2) NOT NULL DEFAULT 0,
  electricity_bill        NUMERIC(12,2) NOT NULL DEFAULT 0,
  rent_amount             NUMERIC(12,2) NOT NULL DEFAULT 0,
  phone_wifi_amount       NUMERIC(12,2) NOT NULL DEFAULT 0,
  water_tax               NUMERIC(12,2) NOT NULL DEFAULT 0,
  photocopy_amount        NUMERIC(12,2) NOT NULL DEFAULT 0,
  bus_amount              NUMERIC(12,2) NOT NULL DEFAULT 0,
  travel_amount           NUMERIC(12,2) NOT NULL DEFAULT 0,
  postal_amount           NUMERIC(12,2) NOT NULL DEFAULT 0,
  register_amount         NUMERIC(12,2) NOT NULL DEFAULT 0,
  stationery_amount       NUMERIC(12,2) NOT NULL DEFAULT 0,
  electrical_amount       NUMERIC(12,2) NOT NULL DEFAULT 0,
  consumer_amount         NUMERIC(12,2) NOT NULL DEFAULT 0,
  other_expense1_amount   NUMERIC(12,2) NOT NULL DEFAULT 0,
  other_expense2_amount   NUMERIC(12,2) NOT NULL DEFAULT 0,
  daily_guard_amount      NUMERIC(12,2) NOT NULL DEFAULT 0,

  claim_pdf_url TEXT,                         -- பின்னர் (PDF கட்டத்தில்) நிரப்பப்படும்
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_claims_lib_month ON claims(library_name, month_year);

-- சில்லறை செலவின அனுமதி (Admin): அனுமதிக்கப்பட்டதா, எப்போது, அனுமதிக்கப்பட்ட மொத்தத் தொகை
ALTER TABLE claims ADD COLUMN IF NOT EXISTS approved        BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE claims ADD COLUMN IF NOT EXISTS approved_at     TIMESTAMPTZ;
ALTER TABLE claims ADD COLUMN IF NOT EXISTS approved_amount NUMERIC(14,2);

-- 18 நாளிதழ் slot தொகைகள் (பழைய 18 column-களுக்குப் பதிலாக)
CREATE TABLE IF NOT EXISTS claim_newspapers (
  claim_id INT NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
  slot     INT NOT NULL CHECK (slot BETWEEN 1 AND 18),
  amount   NUMERIC(12,2) NOT NULL DEFAULT 0,
  PRIMARY KEY (claim_id, slot)
);

-- ஊதிய மையம் (Pay Centre): ஒவ்வொரு நூலகத்துக்கும் ஒன்று
ALTER TABLE libraries ADD COLUMN IF NOT EXISTS pay_center TEXT NOT NULL DEFAULT '';

-- Admin சேர்க்க / திருத்த / நீக்கக்கூடிய பட்டியல்கள்: நூலக வகைகள், ஊதிய மையங்கள்
CREATE TABLE IF NOT EXISTS library_types (
  id   SERIAL PRIMARY KEY,
  name TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS pay_centers (
  id   SERIAL PRIMARY KEY,
  name TEXT NOT NULL UNIQUE
);
-- ஏற்கனவே நூலகங்களில் உள்ள வகை / ஊதிய மையங்களைப் பட்டியலில் சேர்க்கும் (மீண்டும் இயக்கினாலும் நகல் வராது)
INSERT INTO library_types(name)
  SELECT t FROM (SELECT btrim(lib_type) AS t, MIN(id) AS m FROM libraries
                 WHERE btrim(lib_type) <> '' GROUP BY 1) x ORDER BY m
  ON CONFLICT (name) DO NOTHING;
INSERT INTO pay_centers(name)
  SELECT t FROM (SELECT btrim(pay_center) AS t, MIN(id) AS m FROM libraries
                 WHERE btrim(pay_center) <> '' GROUP BY 1) x ORDER BY m
  ON CONFLICT (name) DO NOTHING;

ALTER TABLE claims ADD COLUMN IF NOT EXISTS daily_guard_amount NUMERIC(12,2) NOT NULL DEFAULT 0;

-- கழிவறை சுத்தம்: Admin மட்டும் நிரப்பும் தலைப்பு (நூலகர் பதிவில் இது மாறாது)
ALTER TABLE claims ADD COLUMN IF NOT EXISTS toilet_cleaning_amount NUMERIC(12,2) NOT NULL DEFAULT 0;
