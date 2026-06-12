-- SELECT source_idx, source_name,
--         era_field, harzing_field, wos_categories, 
--         subfield_name, field_name
--     FROM 'data/source_master.csv'
--     LIMIT 16;
--


COPY (
    WITH scores AS (
        SELECT
            *,
            (CASE WHEN regexp_matches(LOWER(era_field),       'econom|financ|banking') THEN 1 ELSE 0 END
               +  CASE WHEN regexp_matches(LOWER(harzing_field),  'econom|financ|banking') THEN 1 ELSE 0 END
               +  CASE WHEN regexp_matches(LOWER(wos_categories), 'econom|financ|banking') THEN 1 ELSE 0 END
               +  CASE WHEN regexp_matches(LOWER(field_name),     'econom|financ|banking') THEN 1 ELSE 0 END
            ) AS econ_score,
            (CASE WHEN regexp_matches(LOWER(era_field),       'business|commerc|management|tourism|transport') THEN 1 ELSE 0 END
               +  CASE WHEN regexp_matches(LOWER(harzing_field),  'business|commerc|management|tourism|transport') THEN 1 ELSE 0 END
               +  CASE WHEN regexp_matches(LOWER(wos_categories), 'business|commerc|management|tourism|transport') THEN 1 ELSE 0 END
               +  CASE WHEN regexp_matches(LOWER(field_name),     'business|commerc|management|tourism|transport') THEN 1 ELSE 0 END
            ) AS bus_score
        FROM 'data/source_master.csv'
    )
    SELECT *,
        CASE
            WHEN econ_score >= 2 AND bus_score  < 1 THEN 'E'
            WHEN bus_score  >= 2 AND econ_score < 1 THEN 'B'
            WHEN bus_score <= 1 and econ_score <= 1 THEN NULL
            ELSE 'A'
        END AS field_eb
    FROM scores
) TO 'data/source_master_new.csv' (FORMAT CSV, HEADER true);



-- SELECT DISTINCT ON (s.id) 
--                 s.id AS source_id, 
--                 s.display_name AS source_name,
--                 works_count, cited_by_count,
--                 s.issn_l AS issn, j.era_journal_name, j.harzing_journal_name, j.wos_journal_name
--     FROM '/home/lc/m/working/econ_bus/parquet/comprehensive_journal_list.parquet' j
--     LEFT JOIN '/home/lc/m/openalex_feb26/parquet/sources.parquet' s
--     ON list_has_any(j.unique_issn_list, s.issn)
--     AND j.unique_issn_list IS NOT NULL AND s.issn IS NOT NULL;
--