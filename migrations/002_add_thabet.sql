ALTER TABLE ecn_profiles DROP CONSTRAINT ecn_profiles_slug_check;
ALTER TABLE ecn_profiles ADD CONSTRAINT ecn_profiles_slug_check
    CHECK (slug IN ('yasmine', 'houssein', 'autre', 'thabet'));
