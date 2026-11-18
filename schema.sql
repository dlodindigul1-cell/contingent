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

  claim_pdf_url TEXT,                         -- பின்னர் (PDF கட்டத்தில்) நிரப்பப்படும்
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_claims_lib_month ON claims(library_name, month_year);

-- 18 நாளிதழ் slot தொகைகள் (பழைய 18 column-களுக்குப் பதிலாக)
CREATE TABLE IF NOT EXISTS claim_newspapers (
  claim_id INT NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
  slot     INT NOT NULL CHECK (slot BETWEEN 1 AND 18),
  amount   NUMERIC(12,2) NOT NULL DEFAULT 0,
  PRIMARY KEY (claim_id, slot)
);
