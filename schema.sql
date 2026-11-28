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

-- தினக்கூலி தொகை நேரடியாகச் சேமிப்பு: நாட்கள் × (பதிவு நேரத்து) கூலி விகிதம் = தொகை; பின்னர் விகிதம் மாறினாலும் மாறாது.
-- (Excel-ல் தொகை மட்டுமே உள்ள பழைய மாதங்களும் இதே நெடுவரிசைகளில் சேமிக்கப்படும்)
ALTER TABLE claims ADD COLUMN IF NOT EXISTS partner_amount   NUMERIC(12,2) NOT NULL DEFAULT 0;
ALTER TABLE claims ADD COLUMN IF NOT EXISTS staff_amount     NUMERIC(12,2) NOT NULL DEFAULT 0;
ALTER TABLE claims ADD COLUMN IF NOT EXISTS part_time_amount NUMERIC(12,2) NOT NULL DEFAULT 0;

-- ஏற்கனவே நாட்களுடன் சேமித்த பதிவுகளுக்கு (தொகை இன்னும் 0 எனில்) தற்போதைய விகிதத்தில் ஒருமுறை நிரப்பு
UPDATE claims SET
  partner_amount   = CASE WHEN partner_amount = 0 AND partner_days > 0
                     THEN partner_days * COALESCE((SELECT rate FROM wage_rates WHERE kind = 'partner'), 75) ELSE partner_amount END,
  staff_amount     = CASE WHEN staff_amount = 0 AND staff_days > 0
                     THEN staff_days * COALESCE((SELECT rate FROM wage_rates WHERE kind = 'staff'), 300) ELSE staff_amount END,
  part_time_amount = CASE WHEN part_time_amount = 0 AND part_time_staff_days > 0
                     THEN part_time_staff_days * COALESCE((SELECT rate FROM wage_rates WHERE kind = 'part_time'), 100) ELSE part_time_amount END
WHERE (partner_amount = 0 AND partner_days > 0) OR (staff_amount = 0 AND staff_days > 0)
   OR (part_time_amount = 0 AND part_time_staff_days > 0);

-- நாளிதழ்கள் — மொத்தத் தொகை (பழைய மாதங்கள் Excel-ல் 18 நாளிதழ்களின் கூட்டுத்தொகையாக மட்டுமே உள்ளன)
ALTER TABLE claims ADD COLUMN IF NOT EXISTS newspaper_total_amount NUMERIC(12,2) NOT NULL DEFAULT 0;

-- Excel மூலம் ஏற்றி (login_email காலி), அனுமதிக்கப்பட்டு, தினத்தந்தி (slot 1) தொகை மட்டுமே உள்ள பதிவுகளில்
-- அந்தத் தொகையை நாளிதழ் மொத்தத்துக்கு நகர்த்து (மொத்த அனுமதித் தொகை மாறாது). மீண்டும் இயக்கினாலும் தீங்கில்லை.
WITH mv AS (
  SELECT c.id, n.amount
  FROM claims c
  JOIN claim_newspapers n ON n.claim_id = c.id AND n.slot = 1
  WHERE c.approved AND COALESCE(c.login_email, '') = ''
    AND NOT EXISTS (SELECT 1 FROM claim_newspapers o WHERE o.claim_id = c.id AND o.slot <> 1)
), upd AS (
  UPDATE claims c SET newspaper_total_amount = c.newspaper_total_amount + mv.amount
  FROM mv WHERE c.id = mv.id RETURNING c.id
)
DELETE FROM claim_newspapers WHERE slot = 1 AND claim_id IN (SELECT id FROM upd);


-- ============================================================
--  Vendor விவரம் (நாளிதழ் vendor / வாடகை — கட்டட உரிமையாளர்): நேரடிப் பணம் செலுத்த
--  kind = paper (நாளிதழ்) | rent (வாடகை). ஒரு vendor பல நூலகங்களுக்கு இருக்கலாம்; ஒரு நூலகத்துக்கு (வாடகைக்கு) 2 vendor-உம் இருக்கலாம்.
--  rent_share = வாடகை vendor-இன் மாதத் தொகை (2 vendor இருக்கும்போது வாடகையைப் பிரிக்க; விருப்பம்)
-- ============================================================
CREATE TABLE IF NOT EXISTS vendors (
  id          SERIAL PRIMARY KEY,
  kind        TEXT NOT NULL CHECK (kind IN ('paper', 'rent')),
  library_id  INT  NOT NULL REFERENCES libraries(id) ON DELETE CASCADE,
  vendor_code TEXT NOT NULL,
  name        TEXT NOT NULL,
  pan_no      TEXT NOT NULL DEFAULT '',
  bank_name   TEXT NOT NULL DEFAULT '',
  branch      TEXT NOT NULL DEFAULT '',
  ifsc        TEXT NOT NULL DEFAULT '',
  account_no  TEXT NOT NULL DEFAULT '',
  rent_share  NUMERIC(12,2) NOT NULL DEFAULT 0,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (kind, library_id, vendor_code)
);
CREATE INDEX IF NOT EXISTS idx_vendors_lib  ON vendors(library_id);
CREATE INDEX IF NOT EXISTS idx_vendors_code ON vendors(kind, vendor_code);


-- காலவரம்புக்கு அப்பால் குறிப்பிட்ட நூலகருக்கு வழங்கப்படும் ஒருமுறை பதிவு அனுமதி
CREATE TABLE IF NOT EXISTS late_registration_permissions (
  id SERIAL PRIMARY KEY, library_name TEXT NOT NULL, month_year TEXT NOT NULL,
  granted_by TEXT NOT NULL DEFAULT 'Admin', granted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (library_name, month_year)
);


-- காலவரம்புக்கு அப்பாலான அனுமதிகளின் நிரந்தர வரலாறு (யாருக்கு, எப்போது, யார் வழங்கினார், எப்போது பயன்படுத்தப்பட்டது)
CREATE TABLE IF NOT EXISTS late_registration_log (
  id SERIAL PRIMARY KEY, library_name TEXT NOT NULL, month_year TEXT NOT NULL,
  granted_by TEXT NOT NULL DEFAULT 'Admin', granted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  status TEXT NOT NULL DEFAULT 'granted',          -- granted / used / revoked
  used_at TIMESTAMPTZ, revoked_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_late_log_lib ON late_registration_log(library_name, month_year);


-- காலவரம்புக்கு (மாதம் 16–23) அப்பால் வவுச்சர் PDF பதிவிறக்க Admin அனுமதி (நீக்கும் வரை செல்லும்; வரலாறும் இதுவே)
CREATE TABLE IF NOT EXISTS voucher_download_permissions (
  id SERIAL PRIMARY KEY, library_name TEXT NOT NULL, month_year TEXT NOT NULL,
  granted_by TEXT NOT NULL DEFAULT 'Admin', granted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  status TEXT NOT NULL DEFAULT 'granted',          -- granted / revoked
  revoked_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_voucher_perm_lib ON voucher_download_permissions(library_name, month_year);

-- ============================================================
-- தனித்த நூலக வரவின மேலாண்மை (பழைய contingent அட்டவணைகளை மாற்றாது)
-- ============================================================
CREATE TABLE IF NOT EXISTS receipt_statement_batches (
 id BIGSERIAL PRIMARY KEY, month_year TEXT NOT NULL, filename TEXT NOT NULL,
 uploaded_by TEXT NOT NULL DEFAULT '', uploaded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS library_receipts (
 id BIGSERIAL PRIMARY KEY, library_name TEXT NOT NULL REFERENCES libraries(name),
 month_year TEXT NOT NULL, security_amount NUMERIC(14,2) NOT NULL DEFAULT 0,
 annual_amount NUMERIC(14,2) NOT NULL DEFAULT 0, newspaper_amount NUMERIC(14,2) NOT NULL DEFAULT 0,
 total_amount NUMERIC(14,2) NOT NULL, status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved')),
 created_by TEXT NOT NULL DEFAULT '', created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 approved_by TEXT, approved_at TIMESTAMPTZ, UNIQUE(library_name,month_year)
);
CREATE TABLE IF NOT EXISTS receipt_bank_transactions (
 id BIGSERIAL PRIMARY KEY, batch_id BIGINT REFERENCES receipt_statement_batches(id) ON DELETE CASCADE,
 month_year TEXT NOT NULL, transaction_date DATE NOT NULL, upi_number TEXT NOT NULL UNIQUE,
 amount NUMERIC(14,2) NOT NULL, status TEXT NOT NULL DEFAULT 'available' CHECK(status IN ('available','reserved','used')),
 library_name TEXT, receipt_id BIGINT REFERENCES library_receipts(id) ON DELETE SET NULL,
 uploaded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_receipt_tx_month_status ON receipt_bank_transactions(month_year,status);
-- UPI எண் மட்டும் (இலக்கங்கள்) சேமிக்கப்படும்; பணம் செலுத்தியவர் பெயர் தனி நெடுவரிசையில்
ALTER TABLE receipt_bank_transactions ADD COLUMN IF NOT EXISTS payer_name TEXT NOT NULL DEFAULT '';
DO $$
BEGIN
  -- பழைய பதிவுகள் ("UPI/CR/627901018885/ANANTHAK/SBIN/...") -> எண் மட்டும் + பெயர்
  UPDATE receipt_bank_transactions t
     SET payer_name = btrim(split_part(t.upi_number,'/',4)),
         upi_number = split_part(t.upi_number,'/',3)
   WHERE t.upi_number ~ '^UPI/CR/[0-9]+'
     AND NOT EXISTS (SELECT 1 FROM receipt_bank_transactions o
                      WHERE o.upi_number = split_part(t.upi_number,'/',3));
EXCEPTION WHEN OTHERS THEN
  NULL; -- இடம்பெயர்வு தோல்வியுற்றாலும் schema தொடரும்
END $$;


-- நூலகர் வரவு / புள்ளி விவரப் பதிவு (index.html படிவம்)
ALTER TABLE library_receipts ADD COLUMN IF NOT EXISTS mode TEXT NOT NULL DEFAULT 'income';
ALTER TABLE library_receipts ADD COLUMN IF NOT EXISTS late_fee NUMERIC(14,2) NOT NULL DEFAULT 0;
ALTER TABLE library_receipts ADD COLUMN IF NOT EXISTS book_fine NUMERIC(14,2) NOT NULL DEFAULT 0;
ALTER TABLE library_receipts ADD COLUMN IF NOT EXISTS old_book_sale NUMERIC(14,2) NOT NULL DEFAULT 0;
ALTER TABLE library_receipts ADD COLUMN IF NOT EXISTS patron NUMERIC(14,2) NOT NULL DEFAULT 0;
ALTER TABLE library_receipts ADD COLUMN IF NOT EXISTS chief_patron NUMERIC(14,2) NOT NULL DEFAULT 0;
ALTER TABLE library_receipts ADD COLUMN IF NOT EXISTS donor NUMERIC(14,2) NOT NULL DEFAULT 0;
ALTER TABLE library_receipts ADD COLUMN IF NOT EXISTS other_income NUMERIC(14,2) NOT NULL DEFAULT 0;
ALTER TABLE library_receipts ADD COLUMN IF NOT EXISTS payment_date DATE;
ALTER TABLE library_receipts ADD COLUMN IF NOT EXISTS visitors INTEGER NOT NULL DEFAULT 0;
ALTER TABLE library_receipts ADD COLUMN IF NOT EXISTS issues INTEGER NOT NULL DEFAULT 0;
ALTER TABLE library_receipts ADD COLUMN IF NOT EXISTS book_usage INTEGER NOT NULL DEFAULT 0;
ALTER TABLE library_receipts ADD COLUMN IF NOT EXISTS book_stock INTEGER NOT NULL DEFAULT 0;
-- Excel மூலம் ஏற்றிய பழைய வரவினங்களின் UPI எண்கள் (பல இருந்தால் கமாவால் பிரிக்கப்பட்டு) — நூல் இருப்பு 0 ஆகவே இருக்கும்; நூலகர் முதன்முறை பதிவு செய்யும்போது சேர்ப்பார்
ALTER TABLE library_receipts ADD COLUMN IF NOT EXISTS upi_numbers TEXT NOT NULL DEFAULT '';
-- Excel மூலம் ஏற்றிய பதிவில் ஒன்றுக்கு மேற்பட்ட UPI இருந்தால், அவற்றின் பணம் செலுத்திய தேதிகள் (YYYY-MM-DD, கமாவால் பிரிக்கப்பட்டு)
ALTER TABLE library_receipts ADD COLUMN IF NOT EXISTS payment_dates TEXT NOT NULL DEFAULT '';
CREATE TABLE IF NOT EXISTS receipt_admin_audit (
 id BIGSERIAL PRIMARY KEY, transaction_id BIGINT, action TEXT NOT NULL, reason TEXT NOT NULL,
 admin_name TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);


-- செயல்படாத நூலகங்கள்: இந்த நாள் முதல் நூலகம் செயல்படவில்லை (NULL = செயல்படுகிறது).
-- இந்த நாளுக்குப் பிந்தைய மாதங்களில் பதிவுகள், பட்டியல்கள், அறிக்கைகளில் அந்த நூலகம் சேராது.
ALTER TABLE libraries ADD COLUMN IF NOT EXISTS inactive_from DATE;

-- நூலகப் புள்ளி விவரம் (உறுப்பினர் / புரவலர் / பெரும்புரவலர் / கொடையாளர்) — மார்ச் 2024 அடிப்படை எண்ணிக்கை.
-- fee = அந்த நூலகத்தின் உறுப்பினர் காப்புத் தொகை கட்டணம் (காப்புத் தொகை வரவை இதனால் வகுத்து புதிய உறுப்பினர் எண்ணிக்கை கணக்கிடப்படும்).
-- ஏப்ரல் 2024 முதல் எண்ணிக்கைகள் ஒப்புதல் பெற்ற வரவுப் பதிவுகளிலிருந்து தானாகக் கணக்கிடப்படும்.
CREATE TABLE IF NOT EXISTS library_stats_base (
 library_name TEXT PRIMARY KEY,
 fee NUMERIC(10,2) NOT NULL DEFAULT 0,
 members INTEGER NOT NULL DEFAULT 0,
 patrons INTEGER NOT NULL DEFAULT 0,
 chief_patrons INTEGER NOT NULL DEFAULT 0,
 donors INTEGER NOT NULL DEFAULT 0,
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);


-- கடந்த ஆண்டு தணிக்கைப் படிவத்தில் நூலகர் உள்ளிடும் விவரங்கள் (நூலகம் + நிதியாண்டு வாரியாக)
CREATE TABLE IF NOT EXISTS audit_receipt_forms (
 library_name TEXT NOT NULL,
 fy TEXT NOT NULL,                                  -- '2025-26'
 opening_balance NUMERIC(14,2) NOT NULL DEFAULT 0,
 expense NUMERIC(14,2) NOT NULL DEFAULT 0,
 book_opening INTEGER NOT NULL DEFAULT 0,
 book_added INTEGER NOT NULL DEFAULT 0,
 book_withdrawn INTEGER NOT NULL DEFAULT 0,
 saved_by TEXT NOT NULL DEFAULT '',
 saved_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 PRIMARY KEY (library_name, fy)
);

-- நூலகர் வரவினப் பதிவுக் காலம் (Admin அமைப்பது; மாதம் வாரியாக)
CREATE TABLE IF NOT EXISTS receipt_entry_windows (
  month_year TEXT PRIMARY KEY,           -- 'MM/YYYY' (வரவு மாதம்)
  start_date DATE NOT NULL,
  end_date   DATE NOT NULL,
  set_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- நூலகர் விடுப்பு நாட்கள் — மாதம் வாரியாக (காலம்: முந்தைய மாதம் 16 -> குறிப்பிட்ட மாதம் 15)
CREATE TABLE IF NOT EXISTS staff_leave_monthly (
  library_id INT NOT NULL REFERENCES libraries(id) ON DELETE CASCADE,
  month_year TEXT NOT NULL,              -- 'MM/YYYY' = காலம் முடியும் மாதம்
  leave_days NUMERIC(5,1) NOT NULL DEFAULT 0,
  PRIMARY KEY (library_id, month_year)
);


-- வரவினக் கட்டண மடங்கு விதிகள் (Admin திருத்தக்கூடியது): patron / chief_patron / donor
-- (காப்புத் தொகை கட்டணம் நூலகம் வாரியாக library_stats_base.fee-ல் உள்ளது)
CREATE TABLE IF NOT EXISTS receipt_fee_rules (
  key TEXT PRIMARY KEY,
  value NUMERIC(10,2) NOT NULL CHECK (value > 0),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Admin வேண்டுமென்றே ஏற்ற (மடங்கல்லாத) தொகை — மடங்குப் பிழைப் பட்டியலில் இனி வராது
ALTER TABLE library_receipts ADD COLUMN IF NOT EXISTS multiples_ok BOOLEAN NOT NULL DEFAULT false;

-- மடங்கல்லாத தொகைக்கு Admin நிர்ணயித்த புள்ளி விவர எண்ணிக்கை (NULL = தொகையை கட்டணத்தால் வகுத்துக் கணக்கிடும்)
ALTER TABLE library_receipts ADD COLUMN IF NOT EXISTS stat_members NUMERIC(10,2);
ALTER TABLE library_receipts ADD COLUMN IF NOT EXISTS stat_patrons NUMERIC(10,2);
ALTER TABLE library_receipts ADD COLUMN IF NOT EXISTS stat_chief_patrons NUMERIC(10,2);
ALTER TABLE library_receipts ADD COLUMN IF NOT EXISTS stat_donors NUMERIC(10,2);


-- ============================================================
--  சோதனை நூலகம் (Admin சோதனைக்கு மட்டும்) — அறிக்கைகள் / புள்ளி விவரங்கள் / மொத்தங்களில் ஒருபோதும் சேராது
--  is_test = TRUE நூலகம்: பொது நூலகர் பட்டியலில் தெரியாது; /test-login வழியாக மட்டும் தெரியும்.
-- ============================================================
ALTER TABLE libraries ADD COLUMN IF NOT EXISTS is_test BOOLEAN NOT NULL DEFAULT FALSE;

INSERT INTO libraries (name, code, emails, lib_type, pay_center, is_test)
  VALUES ('சோதனை நூலகம்', 'TNDPLTEST', 'test.library@example.com', 'முழு நேர கிளை நூலகம்', '', TRUE)
  ON CONFLICT (name) DO NOTHING;
UPDATE libraries SET is_test = TRUE WHERE name = 'சோதனை நூலகம்';

-- விதிகள் பொருந்த: வாடகை, தினக்கூலி அனுமதி (2 பணியாளர்), விடுப்பு நாட்கள், புள்ளி விவர அடிப்படை, எல்லா நாளிதழ்களும்
INSERT INTO rent (library_id, owner_name, amount)
  SELECT id, 'சோதனை உரிமையாளர்', 5000 FROM libraries WHERE name = 'சோதனை நூலகம்'
  ON CONFLICT (library_id) DO NOTHING;
INSERT INTO daily_wage_permission (library_id, workers)
  SELECT id, 2 FROM libraries WHERE name = 'சோதனை நூலகம்'
  ON CONFLICT (library_id) DO NOTHING;
INSERT INTO staff_leave_days (library_id, leave_days)
  SELECT id, 1 FROM libraries WHERE name = 'சோதனை நூலகம்'
  ON CONFLICT (library_id) DO NOTHING;
INSERT INTO library_stats_base (library_name, fee, members, patrons, chief_patrons, donors)
  VALUES ('சோதனை நூலகம்', 100, 100, 5, 1, 1)
  ON CONFLICT (library_name) DO NOTHING;
INSERT INTO library_newspaper_map (library_id, slot, active)
  SELECT l.id, n.slot, TRUE FROM libraries l, newspapers n WHERE l.name = 'சோதனை நூலகம்' AND n.name <> ''
  ON CONFLICT (library_id, slot) DO NOTHING;

-- அறிக்கை மின்னஞ்சல்கள் அனுப்பிய பதிவு (ஒரு நூலகத்துக்கு மணிக்கு எண்ணிக்கை வரம்புக்கு)
CREATE TABLE IF NOT EXISTS report_email_log (
  id           BIGSERIAL PRIMARY KEY,
  library_name TEXT NOT NULL,
  kind         TEXT NOT NULL,
  month_year   TEXT NOT NULL DEFAULT '',
  sent_to      TEXT NOT NULL DEFAULT '',
  sent_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_report_email_log ON report_email_log(library_name, sent_at);

-- Admin தேர்ந்தெடுத்த நூலகங்களுக்கு அறிக்கை PDF மின்னஞ்சல் இல்லாமல் நூலகர் திரையிலேயே கிடைக்கும் (நேரடி PDF)
CREATE TABLE IF NOT EXISTS library_direct_pdf (
  library_name TEXT PRIMARY KEY,
  enabled_by   TEXT NOT NULL DEFAULT '',
  enabled_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);


-- ============================================================
--  பணியாளர் விடுப்பு மேலாண்மை (Phase 1: தரவு)
-- ============================================================
-- நூலகப் பணியாளர்கள் (ஒரு நூலகத்துக்கு 2 அல்லது அதற்கு மேற்பட்டோர்).
-- தினக்கூலிப் பணியாளருக்கு எண் இல்லை: is_daily_wage = TRUE, காட்டும் பெயர் "தினக்கூலி-<நூலகப் பெயர்>"
CREATE TABLE IF NOT EXISTS library_staff (
  id            SERIAL PRIMARY KEY,
  library_id    INT NOT NULL REFERENCES libraries(id) ON DELETE CASCADE,
  staff_name    TEXT NOT NULL DEFAULT '',
  staff_no      TEXT NOT NULL DEFAULT '',
  is_daily_wage BOOLEAN NOT NULL DEFAULT FALSE,
  seq           INT NOT NULL DEFAULT 1,          -- தினக்கூலி: ஒரு நூலகத்தில் 1, 2 ...
  active        BOOLEAN NOT NULL DEFAULT TRUE,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_staff_no
  ON library_staff(library_id, staff_no) WHERE NOT is_daily_wage;
CREATE UNIQUE INDEX IF NOT EXISTS uq_staff_daily
  ON library_staff(library_id, seq) WHERE is_daily_wage;
CREATE INDEX IF NOT EXISTS idx_staff_library ON library_staff(library_id);

-- மத விடுப்புக்கு அனுமதிக்கப்பட்ட தேதிகள் (Admin ஏற்றுவது)
CREATE TABLE IF NOT EXISTS religious_holidays (
  holiday_date DATE PRIMARY KEY,
  name         TEXT NOT NULL DEFAULT ''
);

-- பணியாளர் விடுப்புப் பதிவுகள் (Phase 2-ல் பயன்படும்)
--  leave_type: permission = அனுமதி, casual = தற்செயல், religious = மத, earned = ஈட்டிய, medical = மருத்துவ
--  day_part  : casual-க்கு FN (முற்பகல்) / AN (பிற்பகல்) / FULL (முழு நாள்); மற்றவைக்கு NULL
--  perm_kind : permission-க்கு 'start' (பணி துவங்கும்போது) / 'end' (பணி முடியும்போது)
CREATE TABLE IF NOT EXISTS staff_leave_records (
  id          BIGSERIAL PRIMARY KEY,
  staff_id    INT NOT NULL REFERENCES library_staff(id) ON DELETE RESTRICT,
  leave_type  TEXT NOT NULL CHECK (leave_type IN ('permission','casual','religious','earned','medical')),
  start_date  DATE NOT NULL,
  end_date    DATE NOT NULL,
  days        NUMERIC(5,1) NOT NULL DEFAULT 1,
  day_part    TEXT CHECK (day_part IN ('FN','AN','FULL')),
  perm_kind   TEXT CHECK (perm_kind IN ('start','end')),
  perm_slot   TEXT NOT NULL DEFAULT '',
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (end_date >= start_date)
);
CREATE INDEX IF NOT EXISTS idx_slr_staff_start ON staff_leave_records(staff_id, start_date);
CREATE INDEX IF NOT EXISTS idx_slr_dates ON staff_leave_records(start_date, end_date);
