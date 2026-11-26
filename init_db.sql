-- GIS Collector: PostgreSQL + PostGIS schema.
--
-- One universal table holds objects from every source in specs/:
--   kadastrova_karta        MVT tiles (EPSG:3857) + REST landuse (lon/lat)
--   dream_damage_chernihiv  REST objects/ideas; coordinates exist for only ~5% of records
--   earthdata_chernihiv     CMR granule footprints; rasters downloaded to disk
--   ecozagroza_chernihiv    sensor points (WGS84)
--   osm_chernihiv, overpass Overpass nodes / ways / relations (WGS84)
--
-- Conventions for collectors:
--   * geom is always stored in EPSG:4326; reproject (e.g. MVT 3857 -> 4326) before insert.
--   * geom is NULL when the source gives no coordinates (most DREAM records,
--     which are located only by KATOTTG code / address kept in metadata).
--   * original_object_id must be unique within a source, so encode the
--     sub-type when the source's ids are not globally unique:
--       OSM:      'node/123', 'way/123', 'relation/123'
--       cadastre: 'kadastr/<cadnum>', 'auctions/<auction_id>', 'sales/<public_url>'
--       DREAM:    'objects/<internal.id>', 'ideas/<internal.id>'
--   * metadata holds the raw source attributes (tags, OCDS fields, CMR links, ...).
--   * local_file_path points to downloaded large files (GeoTIFF/HDF granules, documents).

CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS collected_spatial_data (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    source_id           VARCHAR(100)  NOT NULL,
    original_object_id  VARCHAR(512)  NOT NULL,

    geom                GEOMETRY(Geometry, 4326),
    metadata            JSONB         NOT NULL DEFAULT '{}'::jsonb,
    local_file_path     VARCHAR(1024),

    -- Tracking
    created_at          TIMESTAMPTZ   NOT NULL DEFAULT now(),
    scraped_at          TIMESTAMPTZ   NOT NULL DEFAULT now(),

    -- Manual validation
    is_validated        BOOLEAN       NOT NULL DEFAULT FALSE,
    validation_notes    TEXT,
    validated_at        TIMESTAMPTZ,

    CONSTRAINT uq_collected_source_object UNIQUE (source_id, original_object_id),
    CONSTRAINT chk_collected_metadata_object CHECK (jsonb_typeof(metadata) = 'object')
);

COMMENT ON TABLE  collected_spatial_data IS 'Universal storage for spatial objects collected from all sources in specs/.';
COMMENT ON COLUMN collected_spatial_data.source_id IS 'source_id from the spec file, e.g. osm_chernihiv, kadastrova_karta.';
COMMENT ON COLUMN collected_spatial_data.original_object_id IS 'Object id in the source, prefixed with sub-type where needed (way/123, kadastr/<cadnum>).';
COMMENT ON COLUMN collected_spatial_data.geom IS 'Geometry in WGS84 (EPSG:4326); NULL if the source has no coordinates.';
COMMENT ON COLUMN collected_spatial_data.metadata IS 'Raw source attributes.';
COMMENT ON COLUMN collected_spatial_data.local_file_path IS 'Path to a downloaded large file / raster associated with the object.';
COMMENT ON COLUMN collected_spatial_data.scraped_at IS 'Time of the most recent collection of this object.';
COMMENT ON COLUMN collected_spatial_data.is_validated IS 'Set to TRUE after manual review.';
COMMENT ON COLUMN collected_spatial_data.validated_at IS 'Time of the manual review.';

CREATE INDEX IF NOT EXISTS idx_collected_geom
    ON collected_spatial_data USING GIST (geom);

CREATE INDEX IF NOT EXISTS idx_collected_metadata
    ON collected_spatial_data USING GIN (metadata);
