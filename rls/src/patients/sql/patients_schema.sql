CREATE TABLE IF NOT EXISTS patients (
    id_number BIGINT PRIMARY KEY,
    name TEXT NOT NULL,
    age INT NOT NULL,
    site_id INT NOT NULL,
    zip_code VARCHAR(10) NOT NULL,
    ssn VARCHAR(11) NOT NULL
);

CREATE TABLE IF NOT EXISTS doctors (
    user_name TEXT PRIMARY KEY,
    site_id INT NOT NULL
);

CREATE INDEX IF NOT EXISTS patients_age_idx ON patients (age);
CREATE INDEX IF NOT EXISTS patients_name_idx ON patients (name);
CREATE INDEX IF NOT EXISTS patients_zip_code_idx ON patients (zip_code);
CREATE INDEX IF NOT EXISTS patients_ssn_idx ON patients (ssn);
