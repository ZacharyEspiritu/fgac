CREATE OR REPLACE FUNCTION site_policy_join
(row_site BIGINT, curr_user TEXT)
RETURNS BOOLEAN
AS $$
BEGIN
    RETURN (
        (SELECT site_id
    FROM doctors
    WHERE user_name = curr_user) = row_site
    );
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION site_policy_inline
(row_site BIGINT, curr_user TEXT)
RETURNS BOOLEAN
AS $$
DECLARE
    site_value TEXT;
BEGIN
    site_value := current_setting
('app.site_id', true);
IF site_value IS NULL OR site_value = '' THEN
RETURN FALSE;
END
IF;
    RETURN site_value::BIGINT
= row_site;
END;
$$ LANGUAGE plpgsql;

ALTER TABLE patients ENABLE ROW LEVEL SECURITY;
DROP POLICY
IF EXISTS doctor_read ON patients;
CREATE POLICY doctor_read
    ON patients
    FOR
SELECT
    USING (site_policy_join(site_id, current_user));
